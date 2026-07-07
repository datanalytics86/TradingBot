# TradingBot

Bot autónomo de trading algorítmico en Python, orientado a una cuenta
**paper trading** (dinero simulado) de Alpaca de aproximadamente **USD 500**.
Opera acciones/ETFs líquidos con una estrategia de seguimiento de tendencia
(*trend-following*) sobre barras diarias, y está pensado para correr **una
vez al día, sin intervención humana**, típicamente disparado por `cron`
después del cierre del mercado.

## Qué hace el bot (y qué NO promete)

El bot:

- Descarga datos diarios (Open/High/Low/Close/Volume) de cada símbolo de su
  universo.
- Calcula un conjunto simple de indicadores técnicos (EMAs, SMA de
  tendencia, ATR) y decide una señal: `LONG`, `SHORT` o `FLAT`.
- Dimensiona la posición según reglas de riesgo explícitas (nunca arriesga
  más de un porcentaje fijo del capital por operación).
- Envía órdenes a mercado a Alpaca (modo paper por defecto) y gestiona un
  stop de seguimiento (*trailing stop*) día a día.
- Se apaga solo (circuit breaker) si el equity cae demasiado desde su
  máximo histórico.

**Lo que el bot NO promete:**

- **No hay ninguna garantía de retornos positivos.** Es una estrategia de
  trend-following clásica y simple; puede (y en algunos regímenes de
  mercado, sobre todo laterales, probablemente lo hará) perder dinero.
- El backtest incluido es una simplificación razonable pero no una
  simulación perfecta: no modela slippage variable, liquidez limitada,
  gaps extremos, ni el comportamiento exacto de rebalanceo diario de los
  ETFs inversos.
- Rendimiento pasado (real o backtesteado) no garantiza rendimiento futuro.
- Este proyecto es una herramienta educativa/personal, no asesoría
  financiera. **Opera bajo tu propio riesgo** y, si alguna vez pasas a
  dinero real, hazlo únicamente con capital que puedas permitirte perder.

## Por qué ETFs inversos en vez de shorts directos

Con una cuenta cash de **USD 500** no es posible hacer shorts directos de
acciones: FINRA exige un mínimo de **USD 2,000 de margin** para poder pedir
prestadas acciones y venderlas en corto (y además, con menos de USD 25,000
y más de algunas operaciones intradía por semana, se activarían las reglas
de *Pattern Day Trader*, que este bot evita por diseño al operar swing/daily,
no intradía).

Para poder tomar exposición "corta" al mercado sin margin ni shorts reales,
el bot **compra ETFs inversos** (ej. `SH` para el S&P 500, `PSQ` para el
Nasdaq-100) cuando la señal es `SHORT`. Comprar un ETF inverso es una
operación long normal (no requiere margin ni permisos especiales) y da una
exposición inversa aproximada al índice subyacente en el día.

### Sizing y stop de una posición en el ETF inverso

La señal (`LONG`/`SHORT`) se calcula sobre el símbolo subyacente (ej. `SPY`),
pero cuando la señal es `SHORT` la orden real se envía sobre el **proxy**
(ej. `SH`). Es importante no confundir ambos precios: `SPY` puede cotizar a
~USD 550 mientras que `SH` cotiza a ~USD 13, y sus ATR también son muy
distintos.

Por eso, tanto el **tamaño de la posición** (`position_size`) como el
**stop inicial y el trailing stop** de una posición en el proxy se calculan
siempre con el precio y el ATR **del propio proxy** (se descargan y calculan
sus propios indicadores), nunca con los del subyacente. La posición que abre
el broker siempre es una compra en largo (se opere el subyacente o el
proxy), así que el trailing stop se calcula siempre con dirección `"LONG"`
sobre el símbolo realmente operado (`traded_symbol`). El estado
(`state.json`) guarda, por cada símbolo del universo, tanto el
`traded_symbol` (qué se compró realmente) como el `signal_direction` (la
señal original, `LONG` o `SHORT`, que originó esa posición), para poder
reconciliar correctamente una transición de señal (por ejemplo `LONG` en
`SPY` que pasa a `SHORT`: se cierra `SPY` y se abre `SH` en el mismo ciclo).

## Arquitectura y módulos

```
tradingbot/
  config.py           - Carga y validación de config.yaml y credenciales (.env)
  data.py             - Datos OHLCV y premarket (Polygon → Alpaca → yfinance)
  scanner.py          - Scanner premarket (gap + volumen, top N candidatos)
  strategy.py         - Indicadores (EMA/SMA/ATR) y lógica de señal + trailing stop
  risk.py             - Position sizing, circuit breaker y límite de pérdida diaria
  broker.py           - Wrapper sobre Alpaca (alpaca-py), import perezoso
  backtest.py         - Backtest clásico y avanzado (--advanced: walk-forward, Plotly)
  dashboard_builder.py - Dashboard unificado (equity, drawdown, scanner + Grok)
  dashboard.py        - Dashboard legacy (docs/index.html); usar dashboard_builder
  main.py             - Ciclo diario paper/live con safeguards y resumen diario
  report.py           - Reporte de estado en texto (python3 -m tradingbot.report)
  notify.py           - Notificaciones opcionales por Telegram
scripts/
  optimize_params.py  - Grid search de parámetros → optimization_results.json
tests/                - Tests unitarios con datos sintéticos (sin red)
config.yaml           - Estrategia, riesgo, scanner, backtest, dashboard
.env.example          - Plantilla de credenciales (Alpaca, Polygon, Grok, Telegram)
run_bot.bat           - Lanzador rápido en Windows (cycle, backtest, live, etc.)
.github/workflows/trading-bot.yml - Ejecución automática diaria vía GitHub Actions
```

El diseño separa estrategia (pura, sin I/O) de ejecución (broker, datos), lo
que permite testear la lógica de señal y de riesgo con datos sintéticos, sin
red y sin depender de Alpaca. `broker.py` importa `alpaca-py` de forma
perezosa (dentro de los métodos), así que todo el resto del bot funciona
aunque esa librería no esté instalada; solo se necesita de verdad para
conectarse a Alpaca.

## Setup paso a paso

1. Instalar dependencias:

   ```bash
   pip install -r requirements.txt
   ```

2. Crear tu cuenta de **paper trading** en <https://app.alpaca.markets/>
   (es gratis y usa dinero simulado) y generar un par de API keys desde el
   dashboard de "Paper Trading".

3. Copiar la plantilla de entorno y completar las claves:

   ```bash
   cp .env.example .env
   # editar .env y poner ALPACA_API_KEY / ALPACA_SECRET_KEY
   ```

4. Revisar `config.yaml` (universo, parámetros de estrategia y de riesgo).
   Los valores por defecto están pensados para una cuenta de ~USD 500 y son
   deliberadamente conservadores.

## Cómo correr el backtest

```bash
python3 -m tradingbot.backtest --start 2015-01-01
# o con otra configuración:
python3 -m tradingbot.backtest --start 2018-01-01 --config config.yaml
```

Esto descarga historia de yfinance para cada símbolo del universo, simula
la estrategia barra a barra (ejecutando siempre en el Open de la barra
siguiente a la señal, nunca en el mismo Close que la generó, para evitar
look-ahead bias) y al final imprime: retorno total, CAGR, max drawdown,
Sharpe anualizado, número de operaciones, win rate y las últimas 5
operaciones.

Nota de diseño: en el backtest, la señal `SHORT` se modela como una
posición corta sobre el **mismo símbolo subyacente** (en vez de comprar el
ETF inverso), porque es más simple de simular y económicamente similar a la
posición inversa que se toma en vivo.

## Cómo correr el ciclo diario (paper o live)

```bash
python3 -m tradingbot.main
```

Este comando hace un único ciclo:

1. Lee el estado (`state.json`). Si el **scanner premarket** está activado,
   escanea el mercado y reduce el universo a los mejores candidatos más las
   posiciones ya abiertas.
2. Consulta el equity de la cuenta y revisa el circuit breaker (si se activa, cierra todo, escribe `HALT` y
   notifica por Telegram si está configurado).
3. **Reconcilia el estado contra el broker**: si el broker tiene posiciones
   abiertas en símbolos del universo (o sus proxies) que el estado no
   conocía (por ejemplo, tras una intervención manual o un bug previo), las
   adopta al estado con un stop inicializado desde el precio actual, loguea
   un warning, y **no opera nada nuevo sobre ellas ese ciclo**; se gestionan
   normalmente a partir del ciclo siguiente.
4. Para cada símbolo restante del universo, calcula la señal del día y
   reconcilia la posición (abre, cierra o ajusta el trailing stop).
5. Guarda el estado (escritura atómica, ver más abajo), appendea una fila a
   `equity_history.csv` y envía un resumen por Telegram (si está
   configurado).

Estado (`state.json`) y archivo de parada (`HALT`) se guardan en la raíz del
repo. A diferencia de una versión anterior, **sí se versionan** (no están en
`.gitignore`): el workflow de GitHub Actions (ver más abajo) los commitea de
vuelta al repo después de cada ciclo, ya que los runners de Actions son
efímeros y no persisten archivos entre ejecuciones por sí solos.

### Robustez operativa

- **Escritura atómica de `state.json`**: se escribe primero a un archivo
  temporal en el mismo directorio y luego se reemplaza con `os.replace()`,
  para no dejar nunca el archivo truncado/corrupto si el proceso se
  interrumpe a mitad de la escritura.
- **Validación de frescura de datos**: `fetch_daily` valida que la última
  barra descargada no sea más vieja que 5 días calendario; si lo es, lanza
  un error claro en vez de operar con datos obsoletos (por ejemplo, si
  yfinance degrada o devuelve datos parciales). El backtest, que trabaja
  con datos históricos por diseño, desactiva esta validación
  (`validate_freshness=False`).
- **Reconciliación estado↔broker**: ver el punto 3 más arriba.

## Premarket Scanner

Módulo opcional inspirado en scanners de momentum premarket (estilo bots de
day-trading). **Desactivado por defecto** (`scanner.enabled: false`) para no
alterar el comportamiento del bot de ETFs.

### Qué hace

Antes de calcular señales, el scanner:

1. Arma un pool de candidatos: universo base + `watchlist` + gainers de
   Polygon (si hay API key).
2. Descarga cotizaciones premarket vía **Polygon** (principal), con fallback
   a **Alpaca** y **yfinance**.
3. Filtra símbolos con:
   - gap ≥ `gap_threshold` para acciones (default 5%) o `etf_gap_threshold`
     para ETFs (default 2.5% — umbral más bajo)
   - volumen ≥ `volume_multiplier` × promedio de 20 días (default 2×)
   - precio entre `min_price` y `max_price` (default $5–$500)
   - liquidez mínima (`min_avg_volume`)
4. Calcula un **score** ponderado (gap + volumen + momentum) y devuelve los
   top `max_symbols` (default 15).
5. Ajusta el universo del ciclo: candidatos del scanner + posiciones abiertas.
6. **Capa IA (Grok API real)**: `analyze_with_grok()` llama a xAI y puntúa
   cada candidato (`strong` / `buy` / `watch` / `caution` / `skip`) con
   sugerencia de sizing/riesgo. Caché JSON + fallback heurístico si falla.
7. **Exporta CSV** automáticamente si `export_csv: true`.
8. **Reporte mejorado** con tabla ASCII y dashboard HTML opcional.

### Cómo activarlo

1. Añade `POLYGON_API_KEY` en `.env` (recomendado; sin ella usa Alpaca/yfinance).
2. En `config.yaml`:

   ```yaml
   data:
     primary_source: polygon
     fallback_sources:
       - alpaca
       - yfinance

   scanner:
     enabled: true
     gap_threshold: 0.05
     etf_gap_threshold: 2.5    # 2.5% para SPY/QQQ/IWM
     volume_multiplier: 2.0
     max_symbols: 15
     export_csv: true
     generate_html: true        # dashboard en scanner_results/scanner_report.html
     watchlist:
       - NVDA
       - TSLA
   ```

3. Prueba solo el scanner (sin operar):

   ```bash
   python3 -m tradingbot.main --test-scanner
   python3 -m tradingbot.main --test-scanner --config config.yaml
   ```

4. Ver el CSV exportado:

   ```bash
   # Windows
   type scanner_results\scanner_results_YYYYMMDD.csv
   # Linux/macOS
   cat scanner_results/scanner_results_YYYYMMDD.csv
   ```

5. Ver reporte completo (incluye sección Scanner Results):

   ```bash
   python3 -m tradingbot.report
   ```

6. Abrir dashboard HTML (si `generate_html: true`):

   Abre `scanner_results/scanner_report.html` en el navegador.

7. Ciclo completo con scanner activo:

   ```bash
   python3 -m tradingbot.main
   ```

### Export CSV

Tras cada escaneo se genera `scanner_results/scanner_results_YYYYMMDD.csv` con
columnas: símbolo, tipo (ETF/STOCK), score, gap, volumen, precio, fuente y
análisis Grok (`grok_verdict`, `grok_confidence`, `grok_summary`,
`grok_sizing`, `grok_source`).

El estado del último escaneo también queda en `last_scanner.json` (raíz del
repo) para que `tradingbot.report` lo muestre sin re-escanear.

### Capa IA (Grok API — xAI)

1. Crea cuenta en [console.x.ai](https://console.x.ai/) y genera una API key.
2. Añade en `.env`:

   ```bash
   GROK_API_KEY=xai-tu_key_aqui
   ```

3. Configura el modelo en `config.yaml`:

   ```yaml
   api:
     grok_enabled: true
     grok_model: "grok-4"
     timeout: 15
   ```

4. Instala dependencias: `pip install xai-sdk` (incluido en `requirements.txt`).

5. Prueba el análisis:

   ```bash
   python3 -m tradingbot.main --test-scanner
   python3 -m tradingbot.report   # sección "ANÁLISIS GROK"
   ```

**Comportamiento:** usa `xai-sdk` como cliente principal; si falla, intenta
REST OpenAI-compatible (`https://api.x.ai/v1`). Las respuestas se cachean en
`scanner_results/grok_cache.json`. Sin key o si la API falla → heurística local.

### Notas

- Los símbolos nuevos del scanner **no tienen `short_proxy`**: las señales
  `SHORT` en acciones individuales se ignoran (solo ETFs del universo base
  conservan su proxy inverso).
- El backtest **no usa el scanner**; sigue operando el universo fijo de
  `config.yaml`.
- Ajusta los pesos del score (`gap_weight`, `volume_weight`,
  `momentum_weight`) — deben sumar 1.0.

### Ejemplo de crontab

Si preferís correrlo en un servidor propio en vez de GitHub Actions, para
correrlo automáticamente cada día de mercado, después del cierre de NYSE
(16:00 ET = 20:00 UTC en horario estándar, 20:30 tras el cierre para dar
margen a que se asienten los datos de fin de día; se usa 21:30 UTC como
margen extra que también cubre el horario de verano):

```cron
30 21 * * 1-5 cd /ruta/al/repo && /usr/bin/python3 -m tradingbot.main
```

Ajusta la ruta, el intérprete de Python y el offset horario según tu
servidor (mejor aún si el servidor está en UTC, para no preocuparte por el
horario de verano de EE.UU.). No hace falta redirigir la salida a un archivo
de logs a mano: el bot ya loguea a consola **y** a `logs/bot.log`
(rotativo) por su cuenta (ver "Logs y monitoreo" más abajo).

## Logs, reporte y notificaciones

### Logs a archivo

Además de la consola, cada ciclo escribe a `logs/bot.log` usando un
`RotatingFileHandler` (1 MB por archivo, hasta 3 backups:
`bot.log`, `bot.log.1`, `bot.log.2`, `bot.log.3`). El directorio `logs/` se
crea automáticamente si no existe, y está en `.gitignore` (no se versiona).

### Historial de equity

Al final de cada ciclo exitoso, el bot appendea una fila
`fecha_iso,equity` a `equity_history.csv` en la raíz del repo (crea el
archivo con encabezado si no existe). A diferencia de `logs/`, este archivo
**sí se versiona**: es lo que permite ver la evolución del equity a lo
largo del tiempo aunque el bot corra en un runner efímero de GitHub Actions.

### Comando de reporte

```bash
python3 -m tradingbot.report
```

Sin argumentos, imprime un resumen del estado actual del bot:

- Modo (`paper`/`live`), equity actual y posiciones reales según el broker
  (si hay credenciales de Alpaca configuradas y `alpaca-py` instalado; si
  no, lo indica y sigue mostrando la parte local sin fallar).
- El estado local del bot: qué símbolos cree tener abiertos, qué proxy
  operó en cada caso y el stop vigente.
- Las últimas 10 filas de `equity_history.csv`, con la variación porcentual
  entre filas consecutivas.
- Si existe el archivo `HALT`, lo muestra en grande junto con su contenido.

Funciona sin `alpaca-py` instalado (el import es perezoso) y sin
credenciales: en ese caso simplemente informa que no pudo consultar el
broker y continúa con el resto del reporte.

### Notificaciones por Telegram (opcionales)

El bot puede avisar por Telegram al final de cada ciclo (resumen de
equity, señales y órdenes ejecutadas) y **siempre** que se activa el
circuit breaker o que el archivo `HALT` impide correr el ciclo. Es
completamente opcional: si no configurás las variables de entorno, el bot
simplemente no envía nada (no-op silencioso, no falla).

Cómo crear el bot de Telegram y obtener las variables:

1. Hablá con [@BotFather](https://t.me/BotFather) en Telegram y enviá
   `/newbot`. Seguí las instrucciones (nombre y username del bot) y
   guardá el **token** que te da al final: eso es `TELEGRAM_BOT_TOKEN`.
2. Enviale cualquier mensaje a tu bot recién creado (para que Telegram
   registre la conversación).
3. Obtené tu `chat_id`: abrí en el navegador
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` (reemplazando
   `<TU_TOKEN>`) después de haberle escrito al bot, y buscá el campo
   `"chat":{"id": ...}` en la respuesta JSON. Ese número es
   `TELEGRAM_CHAT_ID`.
4. Completá `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en tu `.env` (ver
   `.env.example`) para correr en local, o como *secrets* del repo si
   usás GitHub Actions (ver más abajo).

`notify.py` usa únicamente `urllib` de la librería estándar (sin
dependencias nuevas) y nunca deja que un fallo de red rompa el ciclo del
bot: cualquier error se atrapa y se loguea como warning.

## Ejecución automática con GitHub Actions

El repo incluye un workflow que corre el ciclo diario del bot
automáticamente, sin necesidad de un servidor propio: `.github/workflows/trading-bot.yml`
(si en tu copia del repo aparece en `deploy/trading-bot.yml` en vez de
`.github/workflows/`, movelo manualmente a `.github/workflows/` — puede
pasar si el token usado para generarlo no tenía permiso `workflow`; ver
comentario al principio del archivo).

El workflow:

- Se dispara solo (`schedule`) con el cron `35 21 * * 1-5` (lunes a
  viernes, 21:35 UTC — después del cierre de NYSE), y también puede
  lanzarse a mano desde la pestaña **Actions** del repo
  (`workflow_dispatch`).
- Instala las dependencias (`pip install -r requirements.txt`) y corre
  `python3 -m tradingbot.main`.
- Al terminar (haya ido bien o mal: `if: always()`), si `state.json` o
  `equity_history.csv` cambiaron (o se creó `HALT`), los commitea de vuelta
  al mismo branch con el mensaje `Actualiza estado del bot [skip ci]` y los
  pushea. Si no hay cambios, no crea un commit vacío.

### Configurar los secrets en GitHub

En el repo, andá a **Settings → Secrets and variables → Actions → New
repository secret** y creá estos 4 secrets:

| Secret                 | Valor                                                    |
|-------------------------|----------------------------------------------------------|
| `ALPACA_API_KEY`        | API key de tu cuenta de Alpaca (paper o live)             |
| `ALPACA_SECRET_KEY`     | Secret key de tu cuenta de Alpaca                        |
| `TELEGRAM_BOT_TOKEN`    | Token del bot de Telegram (opcional, ver arriba)          |
| `TELEGRAM_CHAT_ID`      | Chat ID de Telegram (opcional, ver arriba)                |
| `POLYGON_API_KEY`       | Polygon.io para scanner/datos (opcional)                    |
| `GROK_API_KEY`          | xAI Grok para análisis del scanner (opcional)             |

Si no querés notificaciones de Telegram, simplemente no crees esos dos
secrets: el bot corre igual, sin notificar (no-op silencioso).

### Notas importantes sobre el workflow

- **Corre solo días hábiles** (lunes a viernes, `1-5` en el cron). Los
  feriados de mercado (que no coinciden siempre con fines de semana) no
  están filtrados explícitamente: el bot simplemente no encontrará barras
  nuevas relevantes o la validación de frescura de datos podría fallar en
  casos extremos; no es un problema práctico para una estrategia diaria.
- **El `schedule` de GitHub Actions puede retrasarse** varios minutos (a
  veces más, en horas de carga alta de la infraestructura de Actions)
  respecto a la hora exacta configurada en el cron. Para una estrategia de
  swing/diaria como esta, ese retraso es irrelevante.
- El workflow necesita permiso de escritura sobre el repo
  (`permissions: contents: write`) para poder commitear `state.json` y
  `equity_history.csv` de vuelta. Si tu organización restringe los permisos
  por defecto del `GITHUB_TOKEN`, asegurate de que el workflow tenga
  permiso de escritura habilitado (Settings → Actions → General →
  Workflow permissions → "Read and write permissions").

## Checklist para pasar a cuenta real (10 puntos clave)

Antes de cambiar `mode: live` en `config.yaml`, verifica **cada punto**.
Si alguno falla, permanece en paper.

### 1. Backtest validado en múltiples regímenes

- [ ] Corriste `python -m tradingbot.backtest --start 2015-01-01 --advanced`
- [ ] Revisaste Sharpe, Sortino, Calmar, Profit Factor y Max DD
- [ ] Walk-forward OOS no muestra colapso sistemático en todos los folds
- [ ] Expectativa realista: CAGR modesto (~1–3%), no 20% anual

### 2. Paper trading ≥ 8 semanas sin incidentes

- [ ] ≥ 30 ciclos diarios exitosos (GitHub Actions o local)
- [ ] 0 errores críticos sin resolver en las últimas 2 semanas
- [ ] Drawdown paper < 15% y explicable

### 3. Entiendes cada trade del bot

- [ ] Puedes explicar por qué abrió/cerró cada posición
- [ ] Conoces la diferencia entre señal en subyacente y orden en proxy inverso
- [ ] Revisaste el plan en `docs/plan-validacion-paper.md`

### 4. Capital y sizing reales definidos

- [ ] Capital live = solo dinero que puedes perder (recomendado: $500)
- [ ] `risk_per_trade` ajustado (1–1.5% en live, más conservador que paper)
- [ ] Sizing probado con equity paper reseteado a ~$500 si fue posible

### 5. Credenciales y secrets separados

- [ ] API keys **live** distintas a paper en `.env` y GitHub Secrets
- [ ] `GROK_API_KEY` y `POLYGON_API_KEY` configuradas si usas scanner
- [ ] Telegram funcionando para alertas de circuit breaker y HALT

### 6. Safeguards activos

- [ ] `max_drawdown` (circuit breaker) configurado (15% o menos)
- [ ] `max_daily_loss` configurado (3% recomendado)
- [ ] Confirmación live: `--confirm-live` o `TRADINGBOT_LIVE_CONFIRM=YES`
- [ ] Archivo `HALT` ausente antes del día D

### 7. Estado limpio el día D

- [ ] Reset de `state.json`, `equity_history.csv`, `last_run.json`
- [ ] Borrar `HALT` si existía de pruebas
- [ ] Commitear config live y pushear a GitHub

### 8. Monitoreo post-live

- [ ] Dashboard habilitado (`dashboard.enabled: true`)
- [ ] Revisar `docs/dashboard.html` y Telegram tras el primer ciclo
- [ ] Plan de revisión diaria la primera semana (no “instalar y olvidar”)

### 9. Plan de contingencia

- [ ] Sabes cómo detener el bot manualmente (crear `HALT` o desactivar workflow)
- [ ] Sabes cerrar posiciones en Alpaca manualmente si hace falta
- [ ] Tienes contacto/nota de soporte de Alpaca por si hay rechazos de órdenes

### 10. Decisión consciente (go / no-go)

- [ ] Backtest + paper + checklist = **SÍ** en todos los puntos anteriores
- [ ] Aceptas que el bot puede perder dinero incluso con todo bien configurado
- [ ] Primera semana live: monitoreo intensivo, sin cambiar parámetros por pánico

**Comando para arrancar en live (solo tras completar el checklist):**

```bash
# Windows
set TRADINGBOT_LIVE_CONFIRM=YES
python -m tradingbot.main --confirm-live

# o
run_bot.bat live
```

---

## Backtest avanzado y optimización

```bash
# Backtest clásico
python -m tradingbot.backtest --start 2018-01-01

# Backtest avanzado (slippage variable, walk-forward, Plotly)
python -m tradingbot.backtest --start 2018-01-01 --advanced

# Optimizar parámetros (grid search → optimization_results.json)
python scripts/optimize_params.py --start 2018-01-01 --quick

# Dashboard unificado
python -m tradingbot.main --build-dashboard
# Abre docs/dashboard.html en el navegador
```

## Pipeline recomendado: backtest -> paper -> real

1. **Backtest** sobre varios años y regímenes de mercado distintos (alcista,
   bajista, lateral). Ajusta parámetros solo si tiene sentido económico, no
   para "cazar" el mejor número histórico (overfitting).
2. **Paper trading** durante 1-3 meses corriendo el ciclo diario real contra
   la cuenta paper de Alpaca. Esto valida que la ejecución real (fills,
   fines de semana, feriados, datos que llegan tarde o incompletos) se
   comporta como esperas, sin arriesgar dinero real.
3. Solo después de un backtest razonable **y** un período de paper trading
   sin sorpresas, considera pasar a `mode: live` con capital real (y
   empezando con el mínimo que estés dispuesto a perder).

No te apures a pasar a real: el paper trading es gratis, el mercado real no
perdona errores de configuración.

## Parámetros de riesgo, circuit breaker y archivo HALT

Todos los parámetros de riesgo viven en `config.yaml`, bajo `risk:` y
`strategy:`:

- **`risk_per_trade` (2%)**: fracción del equity que el bot está dispuesto a
  perder en una operación si el precio llega exactamente al stop. El tamaño
  de la posición se calcula para que `qty * distancia_al_stop ≈ equity *
  risk_per_trade`.
- **`max_position_pct` (45%)**: tope duro al tamaño nominal de cualquier
  posición individual, independientemente de lo que diga el sizing por
  riesgo (protege contra ATR muy bajos que sizarían posiciones enormes).
- **`max_drawdown` (15%)**: umbral del **circuit breaker**. Si el equity cae
  por debajo de `peak_equity * (1 - max_drawdown)` (es decir, más de un 15%
  desde su máximo histórico), el bot:
  1. Cierra todas las posiciones abiertas (`close_all_positions`).
  2. Crea el archivo `HALT` en la raíz del repo, con el motivo y el
     timestamp.
  3. Termina con código de salida distinto de cero.
- **`atr_stop_mult` (2.5)**: multiplicador del ATR usado tanto para el stop
  inicial como para el trailing stop. Un valor más alto da más espacio a la
  operación (menos stops prematuros) pero también implica perder más si el
  stop se activa.
- **`ema_fast` / `ema_slow` / `trend_filter`**: definen la señal de
  tendencia (cruce de EMAs, filtrado por la SMA de largo plazo).

### Archivo `HALT`

Mientras exista un archivo `HALT` en la raíz del repo, `python3 -m
tradingbot.main` se niega a operar: imprime el motivo guardado en el
archivo y termina con código 1, sin tocar el broker. Es la forma en que el
bot se "apaga solo" y evita seguir operando después de un circuit breaker,
un error grave, o una intervención manual (podés crear el archivo `HALT` a
mano en cualquier momento para pausar el bot). Para reanudar, borra el
archivo `HALT` una vez que entiendas y hayas resuelto la causa.

## Cómo pasar a `mode: live`

1. Cambia `mode: paper` por `mode: live` en `config.yaml`.
2. Genera y configura API keys de **cuenta real** (no las de paper) en
   `.env`. `broker.py` usa el flag `paper` que viene de `cfg.mode`, así que
   con `live` se conectará a la cuenta real de Alpaca.
3. Asegúrate de que la cuenta real tenga fondeado solo el capital que estás
   dispuesto a perder (el diseño está pensado para ~USD 500, no para todos
   tus ahorros).
4. Vuelve a revisar `config.yaml`: los mismos parámetros de riesgo que
   probaste en paper ahora arriesgan dinero real.

**No te apures.** El paso de paper a live no debería hacerse solo porque
"ya pasó un tiempo": hazlo cuando el backtest y el paper trading te den
confianza real en la estrategia, con expectativas realistas sobre drawdowns
y rachas perdedoras (toda estrategia de trend-following tiene rachas
perdedoras largas en mercados laterales; eso no es necesariamente una señal
de que algo está roto).
