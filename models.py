"""
Models — SQLite database (kupitia SQLAlchemy) kwa:
- Settings (number_of_markets, min_probability) - admin anaweza kubadilisha
- ApiConfig (AllSportsAPI key + tarehe ya kuisha)
"""
from flask_sqlalchemy import SQLAlchemy
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
