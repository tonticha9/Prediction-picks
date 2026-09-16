# Braiton Picks

Flask app inayoonyesha football predictions, value bets, na combos
kutoka mfumo wa Kaggle (FAST_models_v6 + Pro Confidence).

## Kuanzisha kwa mara ya kwanza (kwenye simu/kompyuta)

```bash
pip install -r requirements.txt
python app.py
```

Fungua http://localhost:5000

## Kuweka data mpya

1. Toka Kaggle, pakua faili 3: `predictions.csv`, `value_bets.csv`, `combos.csv`
   (kutoka FLASK_PREDICTIONS_v5.csv, FLASK_VALUE_BETS_v5.csv, FLASK_COMBOS_v5.csv —
   badilisha majina kuwa haya matatu HASA)
2. Weka kwenye folder ya `data/`
3. Ingia Admin Panel (`/admin`) kwenye app, bonyeza "Refresh Data"

## Admin

- URL: `/admin`
- Password ya default: `badilisha-hii` (badilisha kwa ENV variable `ADMIN_PASSWORD` kwenye Render)

## Deployment (Render)

1. Push code hii GitHub
2. Render → New → Web Service → unganisha GitHub repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Environment variables: `ADMIN_PASSWORD`, `SECRET_KEY`
