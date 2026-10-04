"""Picha ya kila siku ya predictions (kwa ajili ya History)."""
from models import db


class PredictionDaily(db.Model):
    __tablename__ = 'prediction_daily'

    id = db.Column(db.Integer, primary_key=True)
    snap_date = db.Column(db.Date, nullable=False, index=True)
    section = db.Column(db.String(20), nullable=False)
    match_date = db.Column(db.DateTime, nullable=False)
    home_team = db.Column(db.String(120), nullable=False)
    away_team = db.Column(db.String(120), nullable=False)
    market = db.Column(db.String(120), nullable=False)
    probability = db.Column(db.Float)
    real_odds = db.Column(db.Float)
    pro_tier = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint('snap_date', 'section', 'match_date',
                            'home_team', 'away_team', 'market',
                            name='uq_prediction_daily'),
    )
