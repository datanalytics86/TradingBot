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

## Arquitectura y módulos

```
tradingbot/
  config.py    - Carga y validación de config.yaml y de las claves de Alpaca (.env)
  data.py      - Descarga de datos diarios OHLCV vía yfinance
  strategy.py  - Indicadores (EMA/SMA/ATR) y lógica de señal + trailing stop
  risk.py      - Tamaño de posición (position sizing) y circuit breaker
  broker.py    - Wrapper sobre Alpaca (alpaca-py), import perezoso
  backtest.py  - Motor de backtest histórico (python3 -m tradingbot.backtest)
  main.py      - Ciclo diario en vivo/paper (python3 -m tradingbot.main)
tests/         - Tests unitarios con datos sintéticos (sin red)
config.yaml    - Toda la configuración de estrategia/riesgo/universo
.env.example   - Plantilla de credenciales de Alpaca
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

Este comando hace un único ciclo: lee el estado (`state.json`), consulta el
equity de la cuenta, revisa el circuit breaker, y para cada símbolo del
universo calcula la señal del día y reconcilia la posición (abre, cierra o
ajusta el trailing stop). Estado (`state.json`) y archivo de parada
(`HALT`) se guardan en la raíz del repo y **no se versionan** (ver
`.gitignore`).

### Ejemplo de crontab

Para correrlo automáticamente cada día de mercado, después del cierre de
NYSE (16:00 ET = 20:00 UTC en horario estándar, 20:30 tras el cierre para
dar margen a que se asienten los datos de fin de día; se usa 21:30 UTC como
margen extra que también cubre el horario de verano):

```cron
30 21 * * 1-5 cd /ruta/al/repo && /usr/bin/python3 -m tradingbot.main >> logs/bot.log 2>&1
```

Ajusta la ruta, el intérprete de Python y el offset horario según tu
servidor (mejor aún si el servidor está en UTC, para no preocuparte por el
horario de verano de EE.UU.).

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
