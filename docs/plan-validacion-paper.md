# Plan de validación paper — 8 semanas

Objetivo: decidir con datos si el bot está listo para **dinero real**.

## Semana 0 — Preparación (hoy)

- [x] Confirmar que el bot corre **solo en GitHub Actions** (sin tarea local) — *2026-07-10: tarea Windows `TradingBot-DailyCycle` desactivada en T14*
- [ ] Telegram funcionando — *pendiente: `.env` local sin `TELEGRAM_*`*
- [x] Dashboard visible en repo (`docs/index.html`) — *Pages público existe pero desactualizado (2026-07-06); redeploy pendiente*
- [x] Anotar equity inicial paper: **~$100,000** (default Alpaca paper en `equity_history.csv`)
- [ ] Decidir capital objetivo live: $________ (recomendado: $500)
- [ ] Keys Alpaca **reales** en `.env` de esta máquina — *hoy hay placeholders → ciclo local 401*

**Nota:** la cuenta paper de Alpaca tiene ~$100,000 por defecto. El sizing en live con $500 será **mucho menor**. Para validar sizing real, resetea el balance paper a ~$500 si Alpaca lo permite.

**Ver también:** `docs/status-validacion-2026-07-10.md` (chequeo completo al retomar el proyecto).

---

## Semanas 1–2 — Estabilidad técnica

| Métrica | Meta | Semana 1 | Semana 2 |
|---------|------|----------|----------|
| Ciclos GitHub exitosos | ≥ 8/10 días hábiles | | |
| Mensajes Telegram recibidos | 100% días hábiles | | |
| Errores en `last_run.json` | 0 críticos | | |
| Archivo `HALT` creado | No | | |

**Go:** infraestructura estable. **No-go:** fallos repetidos sin explicación.

---

## Semanas 3–4 — Comportamiento de trading

Registrar cada viernes en el dashboard:

| Métrica | Meta orientativa | Sem 3 | Sem 4 |
|---------|------------------|-------|-------|
| Drawdown vs peak | < 10% | | |
| Posiciones abiertas coherentes con señales | Sí | | |
| Órdenes rechazadas en Alpaca | 0 | | |
| Entendés por qué abrió/cerró cada trade | Sí | | |

**Go:** operaciones tienen sentido. **No-go:** trades inexplicables o rechazos de broker.

---

## Semanas 5–6 — Rendimiento y riesgo

| Métrica | Meta orientativa | Sem 5 | Sem 6 |
|---------|------------------|-------|-------|
| Equity vs inicio del plan | No es obligatorio ganar | | |
| Max drawdown acumulado | < 15% | | |
| Circuit breaker activado | No (o entendido si sí) | | |
| Rachas perdedoras | Las tolerás sin pánico | | |

**Go:** drawdown dentro de lo esperado. **No-go:** drawdown > 15% o comportamiento fuera del backtest.

---

## Semanas 7–8 — Decisión go / no-go live

### Checklist final (todos deben ser SÍ)

- [ ] ≥ 30 ciclos diarios exitosos en paper
- [ ] 0 incidentes técnicos sin resolver en las últimas 2 semanas
- [ ] Entendés el sizing con tu capital live real
- [ ] Backtest revisado: expectativa ~1% CAGR (modesto), no 20%
- [ ] Cuenta Alpaca **live** abierta y fondeada (solo capital que podés perder)
- [ ] API keys **live** listas (distintas a paper)
- [ ] Plan para rotar secrets y resetear `state.json` el día D
- [ ] Parámetros live más conservadores definidos (`risk_per_trade` 1–1.5%)

### Día D — pasar a live (solo si todo lo anterior es SÍ)

1. `mode: live` en `config.yaml`
2. Secrets GitHub con keys **live**
3. Reset: `state.json`, `equity_history.csv`, `last_run.json`, borrar `HALT`
4. Disparar workflow manual y verificar en dashboard + Alpaca + Telegram
5. Monitorear intensivamente la primera semana

### No-go — quedarse en paper si

- Drawdown > 15% sin explicación clara
- Fallos técnicos frecuentes
- No entendés las operaciones del bot
- Expectativas de retorno irreales

---

## Registro semanal (copiar cada viernes)

```
Fecha: ____
Equity: ____
Drawdown vs peak: ____%
Posiciones abiertas: ____
Ciclos OK esta semana: ____/__
Notas: ____
```