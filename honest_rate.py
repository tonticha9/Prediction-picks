"""
honest_rate.py — Plugin ya admin_tools (v2, Nov 27 2026)

Inaongeza kwenye templates:
  honest_text(market, probability)  -> "Ilishinda kweli 74% (soko hili, picks 52, siku 60)" au None
  live_quality_label(market)        -> ('nodisc', 'Haitofautishi') | ('goals_flat', 'Ishara hafifu') | None
  is_discriminating(market)         -> True/False

Ikipata kosa lolote, inarudisha None na kurasa zinaendelea kufanya kazi.

v2 (HATUA F1): inapakia pia league_base_rates.py (base rate ya kila ligi x soko) kama ipo; ikikosa, hakuna kinachovunjika.
"""


def register(app, admin_only, app_ns):
    @app.context_processor
    def _honest_ctx():
        def honest_text(market, probability):
            try:
                import market_selection as ms
                return ms.honest_text(market, probability)
            except Exception:
                return None

        def live_quality_label(market):
            try:
                import market_selection as ms
                return ms.live_quality_label(market)
            except Exception:
                return None

        def is_discriminating(market):
            try:
                import market_selection as ms
                return ms.is_discriminating(market)
            except Exception:
                return True

        return {'honest_text': honest_text,
                'live_quality_label': live_quality_label,
                'is_discriminating': is_discriminating}

    try:
        import league_base_rates
        league_base_rates.install(app, admin_only, app_ns)
    except ModuleNotFoundError:
        pass
    except Exception as ex:
        print(f'[honest_rate] league_base_rates imeshindwa kupakia: {type(ex).__name__}: {ex}')