# Estado de validación paper — 2026-07-10

Chequeo hecho al retomar el proyecto en la máquina **T14 Gen 2** (réplica del otro PC).

## Resumen ejecutivo

| Área | Estado | Notas |
|------|--------|-------|
| Código sincronizado con origin | OK | `293d7f9`, 68 tests green |
| GitHub Pages (público) | PARCIAL | Live en https://datanalytics86.github.io/TradingBot/ pero **desactualizado** (2026-07-06, sin Plotly) |
| Dashboard local | OK | Regenerado hoy en `docs/index.html` / `docs/dashboard.html` |
| GitHub Actions (API) | NO VERIFICABLE | Token git `gho_*` → 401 en API Actions (sin scope) |
| Ciclos cloud previos | OK (histórico) | `equity_history.csv` tiene 6 filas 2026-07-06/07 |
| Ciclo paper **local** | FALLÓ | `.env` con placeholders (`tu_api_key`), Alpaca 401 |
| Telegram local | NO CONFIGURADO | vars vacías en `.env` |
| Tarea Windows local | DESACTIVADA hoy | `TradingBot-DailyCycle` → Disabled (cloud-only) |
| Backtest avanzado | OK | CAGR ~0.73%, Sharpe 0.19, MaxDD -5.96% |
| Optimización params | OK | Mejor: ema 15/50, atr_stop 2.5 (CAGR ~1.09%) |
| HALT | OK | No existe |

## Semana 0 — Preparación

- [x] Código y docs del bot al día en esta máquina
- [x] Tarea local desactivada (evita doble ejecución vs Actions)
- [ ] Confirmar que **GitHub Actions** sigue corriendo en días hábiles (requiere login `gh` o PAT con `actions:read`)
- [ ] Telegram funcionando **en esta máquina** (rellenar `TELEGRAM_*` en `.env`)
- [x] Dashboard visible en repo local; Pages público existe pero **hay que redeploy**
- [x] Equity paper anotado: **~$100,000** (default Alpaca paper; ver nota)
- [ ] Decidir capital objetivo live: $________ (recomendado: $500)
- [ ] Rellenar **keys reales** en `.env` local (hoy son placeholders del example)

**Nota sizing:** la cuenta paper ~$100k infla el tamaño de posición vs live con $500. Para validar sizing real, resetear paper a ~$500 si Alpaca lo permite.

## Bloqueadores para operar desde esta máquina

1. **Alpaca:** `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` en `.env` son placeholders → `unauthorized`.
2. **Telegram:** `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` vacíos.
3. **Grok / Polygon:** no configurados en local (`GROK_API_KEY`, `POLYGON_API_KEY`).
4. **GitHub CLI / PAT:** sin auth con scope Actions → no se puede listar runs ni disparar `workflow_dispatch` desde acá.

## Backtest (re-ejecutado 2026-07-10)

Periodo 2018-01 → 2026-07 (config actual del repo):

| Métrica | Valor |
|---------|-------|
| Retorno total | +6.35% |
| CAGR | +0.73% |
| Max drawdown | -5.96% |
| Sharpe | 0.19 |
| Win rate | 38.0% |
| Profit factor | 1.18 |
| Trades | 242 |

Walk-forward OOS: muchos folds mixtos (positivo y negativo); la estrategia es **modesta** y regímenes laterales la castigan.

## Optimización (quick, 16 combos)

| Campo | Mejor |
|-------|--------|
| ema_fast / ema_slow | **15 / 50** (config actual usa 20/50) |
| atr_stop_mult | 2.5 (igual que config) |
| Score / Sharpe / CAGR | 0.244 / 0.28 / +1.09% |
| Max DD | -5.69% |

**No aplicar a ciegas:** el delta vs 20/50 es pequeño; `gap_threshold` del sweep afecta poco al backtest de ETFs (scanner desactivado).

## Paper trading histórico (repo)

- Último ciclo cloud registrado: **2026-07-07** status `ok`, señales FLAT en SPY/QQQ/IWM.
- Sin posiciones abiertas en `state.json`.
- Sin movimiento de equity (100k plano) — coherente con FLAT / sin fills.

## Próximos pasos inmediatos (orden sugerido)

1. Pegar keys paper reales de Alpaca en `.env` (y opcionalmente Telegram/Grok).
2. `python -m tradingbot.main` local hasta que salga equity real.
3. Autenticar `gh auth login` (o PAT) y verificar últimos runs de Actions + secrets.
4. Disparar deploy del dashboard (workflow "Publicar dashboard") o push de `docs/` actualizado.
5. Marcar Semana 1 del plan con el primer viernes de métricas.
