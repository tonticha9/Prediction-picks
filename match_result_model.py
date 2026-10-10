"""
match_result_model.py — Matokeo halisi ya mechi (yanajazwa na Job A kila saa).
Yanatumika na History kuonyesha matokeo halisi ya kila market
(mfano Corners 11 (6-5), Goals 3 (2-1)).
"""
from datetime import datetime

from models import db


class MatchResult(db.Model):
    __tablename__ = 'match_result'
    __table_args__ = (
        db.UniqueConstraint('match_date', 'home_team', 'away_team', name='uq_match_result'),
    )

    id = db.Column(db.Integer, primary_key=True)
    match_date = db.Column(db.Date, nullable=False, index=True)
    home_team = db.Column(db.String(80), nullable=False)
    away_team = db.Column(db.String(80), nullable=False)
    fthg = db.Column(db.Integer)
    ftag = db.Column(db.Integer)
    hthg = db.Column(db.Integer)
    htag = db.Column(db.Integer)
    hc = db.Column(db.Integer)
    ac = db.Column(db.Integer)
    hy = db.Column(db.Integer)
    ay = db.Column(db.Integer)
    hr = db.Column(db.Integer)
    ar = db.Column(db.Integer)
    hst = db.Column(db.Integer)
    ast = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
