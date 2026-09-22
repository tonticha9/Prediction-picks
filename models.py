"""
Models — SQLite (au PostgreSQL/Neon, kupitia DATABASE_URL) kwa:
- Settings (number_of_markets, min_probability) - admin anaweza kubadilisha
- ApiConfig (AllSportsAPI key + tarehe ya kuisha)
- DataSnapshot (rekodi ya History)
- User (MPYA - kwa admin/watumiaji, badala ya password moja tu)
- PredictionRecord (MPYA - historia ya kila prediction + matokeo Won/Lost)
"""
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class Settings(db.Model):
    """Mipangilio inayobadilishwa na admin - kuna safu MOJA tu (id=1)."""
    id = db.Column(db.Integer, primary_key=True)
    max_markets_per_match = db.Column(db.Integer, default=5)   # "number of markets to show"
    min_probability = db.Column(db.Float, default=0.0)          # "minimum probability per market"
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        s = Settings.query.first()
        if not s:
            s = Settings(max_markets_per_match=5, min_probability=0.0)
            db.session.add(s)
            db.session.commit()
        return s


class ApiConfig(db.Model):
    """AllSportsAPI key + tarehe ya kuisha, kwa ukumbusho wa siku zilizobaki."""
    id = db.Column(db.Integer, primary_key=True)
    key_name = db.Column(db.String(100), default='AllSportsAPI')
    api_key = db.Column(db.String(255), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        c = ApiConfig.query.first()
        if not c:
            c = ApiConfig(key_name='AllSportsAPI', api_key=None, expiry_date=None)
            db.session.add(c)
            db.session.commit()
        return c

    def days_until_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - datetime.utcnow().date()).days


class DataSnapshot(db.Model):
    """Rekodi ya kila mara data ilipopakiwa/kusasishwa - kwa ajili ya 'History'."""
    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, default=datetime.utcnow, index=True)
    predictions_path = db.Column(db.String(255))
    value_bets_path = db.Column(db.String(255))
    combos_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ================================================================
# MPYA: User — kwa admin/watumiaji wengi (badala ya password moja tu)
# ================================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='admin')  # 'admin' au 'viewer' baadaye ukihitaji
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @staticmethod
    def find_by_username(username):
        return User.query.filter_by(username=username, is_active=True).first()


# ================================================================
# MPYA: PredictionRecord — historia ya kila prediction + Won/Lost
# ================================================================
class PredictionRecord(db.Model):
    """Kila mstari mmoja = pick moja iliyoonyeshwa kwa mtumiaji siku fulani.
    'result' inajazwa BAADAYE (na market_evaluator.py) mechi ikiisha:
    'PENDING' -> 'WON' / 'LOST' / 'VOID' (mfano mechi ilisitishwa)."""
    id = db.Column(db.Integer, primary_key=True)

    match_date = db.Column(db.DateTime, index=True, nullable=False)
    home_team = db.Column(db.String(120), index=True, nullable=False)
    away_team = db.Column(db.String(120), index=True, nullable=False)

    market = db.Column(db.String(80), index=True, nullable=False)
    section = db.Column(db.String(20), default='prediction')  # 'prediction' / 'value_bet' / 'combo'

    probability = db.Column(db.Float, nullable=True)      # probability_% wakati wa kuonyesha
    real_odds = db.Column(db.Float, nullable=True)
    ev_percent = db.Column(db.Float, nullable=True)
    pro_tier = db.Column(db.String(20), nullable=True)     # PRO_STRONG / PRO_MEDIUM

    result = db.Column(db.String(10), default='PENDING', index=True)  # PENDING/WON/LOST/VOID
    settled_at = db.Column(db.DateTime, nullable=True)

    shown_date = db.Column(db.Date, default=datetime.utcnow, index=True)  # siku ilipoonyeshwa kwenye app
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_match_market', 'match_date', 'home_team', 'away_team', 'market'),
    )
