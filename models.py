"""
Models — SQLite (au PostgreSQL/Neon, kupitia DATABASE_URL) kwa:
- Settings (mipangilio ya admin - SASA na probability_threshold, best_pick_formula, history_max_markets)
- ApiConfig (AllSportsAPI key + tarehe ya kuisha)
- DataSnapshot (rekodi ya History - CSV snapshots za zamani)
- User (admin/watumiaji)
- PredictionRecord (historia ya kila prediction + matokeo Won/Lost)
"""
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class Settings(db.Model):
    """Mipangilio inayobadilishwa na admin - kuna safu MOJA tu (id=1)."""
    id = db.Column(db.Integer, primary_key=True)
    max_markets_per_match = db.Column(db.Integer, default=5)     # Dashboard: "other picks" ngapi
    min_probability = db.Column(db.Float, default=0.0)            # (zamani - haitumiki tena kwa dedup)
    probability_threshold = db.Column(db.Float, default=50.0)     # MPYA: chini ya hii, soko haonekani kabisa
    best_pick_formula = db.Column(db.String(20), default='hybrid')  # MPYA: 'probability' / 'ev' / 'hybrid'
    history_max_markets = db.Column(db.Integer, default=10)       # MPYA: History: "other picks" ngapi
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        s = Settings.query.first()
        if not s:
            s = Settings(max_markets_per_match=5, min_probability=0.0,
                         probability_threshold=50.0, best_pick_formula='hybrid',
                         history_max_markets=10)
            db.session.add(s)
            db.session.commit()
        return s


class ApiConfig(db.Model):
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
    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, default=datetime.utcnow, index=True)
    predictions_path = db.Column(db.String(255))
    value_bets_path = db.Column(db.String(255))
    combos_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='admin')
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


class PredictionRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_date = db.Column(db.DateTime, index=True, nullable=False)
    home_team = db.Column(db.String(120), index=True, nullable=False)
    away_team = db.Column(db.String(120), index=True, nullable=False)
    market = db.Column(db.String(80), index=True, nullable=False)
    section = db.Column(db.String(20), default='prediction')
    probability = db.Column(db.Float, nullable=True)
    real_odds = db.Column(db.Float, nullable=True)
    ev_percent = db.Column(db.Float, nullable=True)
    pro_tier = db.Column(db.String(20), nullable=True)
    result = db.Column(db.String(10), default='PENDING', index=True)
    settled_at = db.Column(db.DateTime, nullable=True)
    shown_date = db.Column(db.Date, default=datetime.utcnow, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_match_market', 'match_date', 'home_team', 'away_team', 'market'),
    )
