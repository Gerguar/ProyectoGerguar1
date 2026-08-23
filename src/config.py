"""
Configuración central del sistema.

Define ligas objetivo, ventanas temporales, paths, hiperparámetros del modelo,
y los horizontes de snapshot (t-7d, t-24h, t-60m).
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MODEL_DIR = DATA_DIR / "models"
WEB_DIR = ROOT / "web"

DATA_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Competition:
    code: str
    name: str
    fd_code: str | None = None
    is_international: bool = False


COMPETITIONS: list[Competition] = [
    # Solo competiciones disponibles en el FREE TIER de football-data.org.
    # UEL y UECL requieren plan pago — se omiten.
    Competition("UCL", "UEFA Champions League", "CL", True),

    Competition("EPL", "Premier League", "PL"),
    Competition("LL",  "La Liga",        "PD"),
    Competition("SA",  "Serie A",        "SA"),
    Competition("BL",  "Bundesliga",     "BL1"),
    Competition("L1",  "Ligue 1",        "FL1"),

    # Liga Argentina — sin fd_code (football-data.org free no la cubre).
    # Se ingiere via football-data.co.uk /new/ARG.csv (ver ingest_couk.py).
    Competition("ARG", "Liga Profesional Argentina", None),
]

INTERNATIONAL_CODES = [c.code for c in COMPETITIONS if c.is_international]
DOMESTIC_CODES = [c.code for c in COMPETITIONS if not c.is_international]


@dataclass(frozen=True)
class EloConfig:
    k_base: float = 20.0
    home_advantage: float = 65.0
    initial_rating: float = 1500.0
    margin_factor: bool = True


@dataclass(frozen=True)
class DixonColesConfig:
    xi: float = 0.0035
    max_goals: int = 8
    min_matches: int = 80


@dataclass(frozen=True)
class XGBConfig:
    objective: str = "multi:softprob"
    eval_metric: str = "mlogloss"
    max_depth: int = 4
    learning_rate: float = 0.03
    n_estimators: int = 2000
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 2.0
    early_stopping_rounds: int = 100
    num_class: int = 3


@dataclass(frozen=True)
class BacktestConfig:
    min_train_matches: int = 500
    valid_window_days: int = 60
    test_window_days: int = 30
    step_days: int = 30
    calibration_method: str = "isotonic"
    # Piso de partidos para la ventana de validacion. Las ventanas se definen en
    # DIAS, asi que un paron largo (Mundial, verano) puede dejarlas casi vacias
    # y degenerar el calibrador y el early stopping. Si no se llega a este piso,
    # train.py corre train_end hacia atras hasta juntarlos.
    min_valid_matches: int = 300


@dataclass(frozen=True)
class Snapshots:
    """
    Cada feature debe corresponder a información disponible en uno de estos
    cortes temporales antes del kickoff. Mezclarlos = leakage.
    """
    far: str = "7d"
    mid: str = "24h"
    near: str = "60m"


@dataclass(frozen=True)
class Paths:
    matches: Path = DATA_DIR / "matches.parquet"
    elo_state: Path = DATA_DIR / "elo_state.json"
    dc_state: Path = DATA_DIR / "dc_state.json"
    xgb_model: Path = MODEL_DIR / "xgb_1x2.json"
    calibrator: Path = MODEL_DIR / "calibrator.joblib"
    feature_meta: Path = MODEL_DIR / "feature_meta.json"
    predictions: Path = DATA_DIR / "predictions.json"
    backtest_report: Path = DATA_DIR / "backtest_report.json"


@dataclass(frozen=True)
class APIKeys:
    football_data: str | None = field(default_factory=lambda: os.getenv("FOOTBALL_DATA_TOKEN"))
    the_odds_api: str | None = field(default_factory=lambda: os.getenv("THE_ODDS_API_KEY"))
    apifootball: str | None = field(default_factory=lambda: os.getenv("API_FOOTBALL_KEY"))


ELO = EloConfig()
DC = DixonColesConfig()
XGB = XGBConfig()
BACKTEST = BacktestConfig()
SNAPSHOTS = Snapshots()
PATHS = Paths()
KEYS = APIKeys()

LABEL_MAP = {"H": 0, "D": 1, "A": 2}
LABEL_INV = {v: k for k, v in LABEL_MAP.items()}
