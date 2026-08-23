"""
Entrenamiento final del modelo de producción.

Esquema:
    train  = partidos finalizados hasta hoy - (valid_window + test_window)
    valid  = bloque siguiente             → calibración + early stopping
    holdout= último bloque                 → reporte de métricas (no entra al fit)

Guarda artefactos en data/models/.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd

from .config import BACKTEST, PATHS, LABEL_MAP
from .dixon_coles import fit as fit_dc
from .elo import EloState, replay
from .features import FeatureBuilder, feature_columns
from .xgb_model import (fit_xgb, IsotonicMulticlassCalibrator,
                        save_artifacts)
from .metrics import (multi_log_loss, multi_brier, accuracy_top1,
                      market_baseline_log_loss, calibration_per_class)


def main() -> None:
    matches = pd.read_parquet(PATHS.matches)
    matches["kickoff_ts_utc"] = pd.to_datetime(matches["kickoff_ts_utc"], utc=True)
    finished = matches.dropna(subset=["home_goals", "away_goals"]).copy()
    finished = finished.sort_values("kickoff_ts_utc").reset_index(drop=True)

    now = pd.Timestamp.now(tz="UTC")
    test_end = now
    valid_end = now - pd.Timedelta(days=BACKTEST.test_window_days)
    train_end = valid_end - pd.Timedelta(days=BACKTEST.valid_window_days)

    # Las ventanas son por DIAS y los parones largos las vacian. En ago-2026 la
    # de validacion (25-may a 24-jul) cayo entera en el hueco del Mundial y
    # quedo con 1 partido: con eso el calibrador isotonico y el early stopping
    # de XGBoost salen degenerados (el holdout dio log_loss 4.78, peor que
    # tirar una moneda). Si no se llega al piso, corremos train_end hacia atras
    # hasta juntar min_valid_matches. Se hace ACA, antes de DC/Elo, para que
    # esos tambien respeten el corte y no vean partidos de validacion.
    en_ventana = ((finished["kickoff_ts_utc"] >= train_end) &
                  (finished["kickoff_ts_utc"] < valid_end)).sum()
    if en_ventana < BACKTEST.min_valid_matches:
        previos = finished[finished["kickoff_ts_utc"] < valid_end]
        if len(previos) > BACKTEST.min_valid_matches:
            train_end = previos["kickoff_ts_utc"].iloc[-BACKTEST.min_valid_matches]
            print(f"[train] ventana de validacion con solo {en_ventana} partidos "
                  f"(paron); se reajusta train_end a {train_end.date()} para "
                  f"juntar ~{BACKTEST.min_valid_matches}")

    # Para DC + Elo entrenamos con TODO el historico (mas data = mejores ratings).
    train_raw_full = finished[finished["kickoff_ts_utc"] < train_end]
    if len(train_raw_full) < BACKTEST.min_train_matches:
        raise RuntimeError(f"Pocos partidos para entrenar DC/Elo: {len(train_raw_full)}")

    print(f"[train] DC+Elo: {len(train_raw_full)} partidos hasta {train_end.date()}")
    # Intentamos entrenar DC con xG si team_xg.parquet existe.
    try:
        from .ingest_xg import load_team_xg
        team_xg_df = load_team_xg()
        if not team_xg_df.empty:
            print(f"[train] usando xG blend (cobertura: {len(team_xg_df)} partidos)")
            dc_state = fit_dc(train_raw_full, asof_ts=train_end,
                              use_xg=True, team_xg=team_xg_df, xg_blend=0.5)
        else:
            print("[train] team_xg.parquet vacio, DC usa goles puros")
            dc_state = fit_dc(train_raw_full, asof_ts=train_end)
    except Exception as e:
        print(f"[train] DC con xG fallo ({e}); usando goles puros")
        dc_state = fit_dc(train_raw_full, asof_ts=train_end)
    dc_state.to_json()
    elo_state = EloState()
    replay(train_raw_full, elo_state)
    elo_state.to_json()

    # Para XGBoost (que usa features de rating EA FC) filtramos a las ultimas
    # 2 temporadas para minimizar lookahead bias (EA ratings actuales aplicados
    # a partidos viejos seria contaminacion).
    xgb_train_from = pd.Timestamp("2024-07-01", tz="UTC")
    print(f"[train] XGBoost: usa partidos desde {xgb_train_from.date()}")

    fb = FeatureBuilder()
    full_feat = fb.build_training_table(finished, dc_state, elo_state)
    full_feat = full_feat.dropna(subset=["label"])
    full_feat = full_feat[full_feat["kickoff_ts_utc"] >= xgb_train_from]
    print(f"[train] features post-filtro: {len(full_feat)} partidos")

    train_feat = full_feat[full_feat["kickoff_ts_utc"] < train_end]
    valid_feat = full_feat[(full_feat["kickoff_ts_utc"] >= train_end) &
                            (full_feat["kickoff_ts_utc"] < valid_end)]
    holdout_feat = full_feat[(full_feat["kickoff_ts_utc"] >= valid_end) &
                              (full_feat["kickoff_ts_utc"] < test_end)]

    print(f"[train] n_train={len(train_feat)}  n_valid={len(valid_feat)}  n_holdout={len(holdout_feat)}")

    clf = fit_xgb(train_feat, valid_feat)

    X_valid = valid_feat.reindex(columns=feature_columns()).astype(float)
    valid_raw = clf.predict_proba(X_valid)
    y_valid = valid_feat["label"].map(LABEL_MAP).astype(int).values
    calibrator = IsotonicMulticlassCalibrator.fit(valid_raw, y_valid)

    holdout_metrics = {}
    if len(holdout_feat) > 0:
        X_ho = holdout_feat.reindex(columns=feature_columns()).astype(float)
        y_ho = holdout_feat["label"].map(LABEL_MAP).astype(int).values
        proba_ho = calibrator.transform(clf.predict_proba(X_ho))
        mkt_p = holdout_feat.reindex(columns=["market_p_home", "market_p_draw", "market_p_away"]).to_numpy(dtype=float)

        holdout_metrics = {
            "n": int(len(holdout_feat)),
            "log_loss": multi_log_loss(y_ho, proba_ho),
            "brier": multi_brier(y_ho, proba_ho),
            "accuracy": accuracy_top1(y_ho, proba_ho),
            "market_log_loss": market_baseline_log_loss(mkt_p, y_ho),
            "calibration": calibration_per_class(y_ho, proba_ho, n_bins=10),
        }
        print(f"[holdout] log_loss={holdout_metrics['log_loss']:.4f}  "
              f"brier={holdout_metrics['brier']:.4f}  "
              f"acc={holdout_metrics['accuracy']:.3f}  "
              f"mkt_ll={holdout_metrics['market_log_loss']}")

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_end": str(train_end),
        "valid_end": str(valid_end),
        "n_train": int(len(train_feat)),
        "n_valid": int(len(valid_feat)),
        "feature_columns": feature_columns(),
        "holdout_metrics": holdout_metrics,
    }
    save_artifacts(clf, calibrator, meta)
    print("[train] artefactos guardados")


if __name__ == "__main__":
    main()
