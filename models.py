"""
Models — SQLite (au PostgreSQL/Neon, kupitia DATABASE_URL) kwa:
- Settings (mipangilio ya admin)
- ApiConfig (AllSportsAPI key + tarehe ya kuisha)
- DataSnapshot (rekodi ya History - CSV snapshots za zamani)
- User (admin/watumiaji)
- PredictionRecord (historia ya kila prediction/value_bet + matokeo Won/Lost)
- ComboRecord + ComboLeg (historia ya combos + legs zake + matokeo)
"""
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class Settings(db.Model):
    """Mipangilio inayobadilishwa na admin - kuna safu MOJA tu (id=1)."""
    id = db.Column(db.Integer, primary_key=True)
    max_markets_per_match = db.Column(db.Integer, default=5)
    min_probability = db.Column(db.Float, default=0.0)
    probability_threshold = db.Column(db.Float, default=50.0)
    best_pick_formula = db.Column(db.String(20), default='hybrid')
    history_max_markets = db.Column(db.Integer, default=10)
    combo_min_legs = db.Column(db.Integer, default=4)
    combo_max_legs = db.Column(db.Integer, default=4)
    combo_min_leg_probability = db.Column(db.Float, default=65.0)
    # MPYA: kizingiti cha probability ya COMBO NZIMA (zidisho la legs zote),
    # si leg moja. Combo yenye combined_probability chini ya hii HAIONYESHWI.
    combo_min_combined_probability = db.Column(db.Float, default=0.0)
    max_combos_per_day = db.Column(db.Integer, default=50)
    # 'random_threshold' | 'ev_odds_only' | 'highest_probability' | 'highest_probability_odds_only'
    combo_leg_formula = db.Column(db.String(30), default='random_threshold')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        s = Settings.query.first()
        if not s:
            s = Settings(max_markets_per_match=5, min_probability=0.0,
                         probability_threshold=50.0, best_pick_formula='hybrid',
                         history_max_markets=10, combo_min_legs=4, combo_max_legs=4,
                         combo_min_leg_probability=65.0, combo_min_combined_probability=0.0,
                         max_combos_per_day=50, combo_leg_formula='random_threshold')
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
    first_probability = db.Column(db.Float, nullable=True)
    first_real_odds = db.Column(db.Float, nullable=True)
    first_pro_tier = db.Column(db.String(20), nullable=True)
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


class ComboRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shown_date = db.Column(db.Date, default=datetime.utcnow, index=True)
    combined_odds = db.Column(db.Float, nullable=True)
    combined_probability = db.Column(db.Float, nullable=True)
    formula_used = db.Column(db.String(30), nullable=True)
    result = db.Column(db.String(10), default='PENDING', index=True)
    settled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    legs = db.relationship('ComboLeg', backref='combo', cascade='all, delete-orphan',
                           order_by='ComboLeg.id')


class ComboLeg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    combo_id = db.Column(db.Integer, db.ForeignKey('combo_record.id'), nullable=False, index=True)
    home_team = db.Column(db.String(120))
    away_team = db.Column(db.String(120))
    match_date = db.Column(db.DateTime)
    market = db.Column(db.String(80))
    probability = db.Column(db.Float)
    real_odds = db.Column(db.Float, nullable=True)
    is_stake_leg = db.Column(db.Boolean, default=True)
    result = db.Column(db.String(10), default='PENDING')
