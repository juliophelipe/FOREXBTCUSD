import requests
import time
import numpy as np
import csv
from datetime import datetime

# === CONFIGURAÇÕES ===
TOKEN = '8082090546:AAFQN5bQWMSTjd9u10Bp8hdoLbpGNP1AlJc'
CHAT_ID = '-1002535692702'
SYMBOL = 'BTCUSDT'
INTERVAL = '5m'
CONFIRM_INTERVAL = '15m'
LIMIT = 50
SL_PERCENT = 0.3
TP_PERCENT = 0.4
STATUS_INTERVAL = 1800  # 30 minutos
CSV_FILE = "sinais_registrados.csv"

# === INDICADORES ===
EMA_PERIOD = 21
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SR_LOOKBACK = 20

# === VARIÁVEIS DE CONTROLE ===
ultimo_sinal = {"tipo": None, "preco": None, "take": None, "stop": None}
ultimo_status = 0
ultima_mensagem_status_id = None
stop_consecutivos = 0
pausado = False
PAUSA_LIMITE = 3
TEMPO_PAUSA = 3600  # 1 hora
sinal_perdido = None
ultima_mensagem_alerta = None  # Controle de duplicação de alertas

# === FUNÇÕES AUXILIARES ===
def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, data=payload)
        if response.ok:
            return response.json()["result"]["message_id"]
    except Exception as e:
        print("Erro ao enviar mensagem:", e)
    return None

def apagar_ultima_mensagem_status():
    global ultima_mensagem_status_id
    if ultima_mensagem_status_id:
        url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
        payload = {"chat_id": CHAT_ID, "message_id": ultima_mensagem_status_id}
        try:
            requests.post(url, data=payload)
        except Exception as e:
            print("Erro ao apagar mensagem:", e)
        ultima_mensagem_status_id = None

def registrar_csv(tipo, entrada, take, stop, probabilidade):
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([datetime.now(), tipo, entrada, take, stop, probabilidade])

def registrar_resultado_csv(resultado):
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([datetime.now(), f"RESULTADO: {resultado.upper()}"])

def calcular_ema(prices, period):
    weights = np.exp(np.linspace(-1., 0., period))
    weights /= weights.sum()
    a = np.convolve(prices, weights, mode='full')[:len(prices)]
    a[:period] = a[period]
    return a

def calcular_rsi(prices, period=14):
    deltas = np.diff(prices)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)
    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def calcular_macd(prices):
    ema_fast = calcular_ema(prices, MACD_FAST)
    ema_slow = calcular_ema(prices, MACD_SLOW)
    macd_line = ema_fast - ema_slow
    signal_line = calcular_ema(macd_line, MACD_SIGNAL)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def detectar_suporte_resistencia(precos):
    suporte = min(precos[-SR_LOOKBACK:])
    resistencia = max(precos[-SR_LOOKBACK:])
    return suporte, resistencia

def zona_perigosa(preco, suporte, resistencia, margem=0.15):
    dist_suporte = abs(preco - suporte) / suporte * 100
    dist_resistencia = abs(resistencia - preco) / resistencia * 100
    return dist_suporte < margem or dist_resistencia < margem

def candle_tem_forca(abertura, fechamento, minimo, maximo, min_corpo=20):
    corpo = abs(fechamento - abertura)
    pavio = maximo - minimo
    return corpo > (pavio * min_corpo / 100)

def calcular_probabilidade(rsi, hist):
    rsi_score = (rsi[-1] - 50) / 50
    hist_score = hist[-1] / max(1e-5, np.std(hist[-10:]))
    prob = (rsi_score + hist_score) / 2
    prob = max(min(prob, 1), -1)
    return round(abs(prob) * 100)

def alerta_antecipado(candles):
    global ultima_mensagem_alerta
    precos_fechamento = np.array([float(c[4]) for c in candles])
    precos_abertura = np.array([float(c[1]) for c in candles])
    precos_max = np.array([float(c[2]) for c in candles])
    precos_min = np.array([float(c[3]) for c in candles])
    preco_atual = precos_fechamento[-1]
    ema = calcular_ema(precos_fechamento, EMA_PERIOD)
    rsi = calcular_rsi(precos_fechamento, RSI_PERIOD)
    macd_line, signal_line, histogram = calcular_macd(precos_fechamento)

    if preco_atual > ema[-1] and rsi[-1] > 53 and histogram[-1] > histogram[-2]:
        mensagem = "⏳ *Alerta antecipado:* possível sinal de COMPRA se o candle confirmar..."
        if mensagem != ultima_mensagem_alerta:
            enviar_telegram(mensagem)
            ultima_mensagem_alerta = mensagem
    elif preco_atual < ema[-1] and rsi[-1] < 47 and histogram[-1] < histogram[-2]:
        mensagem = "⏳ *Alerta antecipado:* possível sinal de VENDA se o candle confirmar..."
        if mensagem != ultima_mensagem_alerta:
            enviar_telegram(mensagem)
            ultima_mensagem_alerta = mensagem

def considerar_reentrada(preco_atual):
    global sinal_perdido, ultimo_sinal
    if sinal_perdido:
        tipo = sinal_perdido["tipo"]
        preco = sinal_perdido["preco"]
        if (tipo == "compra" and preco_atual > preco) or (tipo == "venda" and preco_atual < preco):
            enviar_telegram(f"♻️ *Reentrada após Stop!* Mercado voltou a respeitar a direção de {tipo.upper()}. Nova oportunidade.")
            ultimo_sinal = sinal_perdido
            sinal_perdido = None

def checar_pausa():
    global pausado, stop_consecutivos
    if stop_consecutivos >= PAUSA_LIMITE:
        pausado = True
        enviar_telegram("⚠️ *Pausa de segurança ativada após 3 Stops consecutivos.*\nA Iris irá aguardar 1 hora antes de voltar a operar.")
        time.sleep(TEMPO_PAUSA)
        pausado = False
        stop_consecutivos = 0

def acompanhar_resultado(preco_atual):
    global ultimo_sinal, stop_consecutivos, sinal_perdido
    if not ultimo_sinal["tipo"]:
        return
    if ultimo_sinal["tipo"] == "compra":
        if preco_atual >= ultimo_sinal["take"]:
            enviar_telegram("✅ *Take Profit atingido com elegância!*")
            registrar_resultado_csv("Take")
            ultimo_sinal = {"tipo": None, "preco": None, "take": None, "stop": None}
        elif preco_atual <= ultimo_sinal["stop"]:
            enviar_telegram("❌ *Stop Loss atingido, ajustando nossa mira...*")
            registrar_resultado_csv("Stop")
            sinal_perdido = ultimo_sinal
            stop_consecutivos += 1
            ultimo_sinal = {"tipo": None, "preco": None, "take": None, "stop": None}
            checar_pausa()
    elif ultimo_sinal["tipo"] == "venda":
        if preco_atual <= ultimo_sinal["take"]:
            enviar_telegram("✅ *Take Profit atingido com precisão cirúrgica!*")
            registrar_resultado_csv("Take")
            ultimo_sinal = {"tipo": None, "preco": None, "take": None, "stop": None}
        elif preco_atual >= ultimo_sinal["stop"]:
            enviar_telegram("❌ *Stop Loss atingido, mas seguimos refinando...*")
            registrar_resultado_csv("Stop")
            sinal_perdido = ultimo_sinal
            stop_consecutivos += 1
            ultimo_sinal = {"tipo": None, "preco": None, "take": None, "stop": None}
            checar_pausa()

def analisar_sinal(candles, candles_m15):
    precos_fechamento = np.array([float(c[4]) for c in candles])
    precos_abertura = np.array([float(c[1]) for c in candles])
    precos_max = np.array([float(c[2]) for c in candles])
    precos_min = np.array([float(c[3]) for c in candles])
    precos_m15 = np.array([float(c[4]) for c in candles_m15])

    ema = calcular_ema(precos_fechamento, EMA_PERIOD)
    rsi = calcular_rsi(precos_fechamento, RSI_PERIOD)
    macd_line, signal_line, histogram = calcular_macd(precos_fechamento)
    suporte, resistencia = detectar_suporte_resistencia(precos_fechamento)
    preco_atual = precos_fechamento[-1]

    ema_m15 = calcular_ema(precos_m15, EMA_PERIOD)
    direcao_m15 = "alta" if precos_m15[-1] > ema_m15[-1] else "baixa"

    if zona_perigosa(preco_atual, suporte, resistencia):
        return "neutro", 0, preco_atual, suporte, resistencia

    if not candle_tem_forca(precos_abertura[-1], precos_fechamento[-1], precos_min[-1], precos_max[-1]):
        return "neutro", 0, preco_atual, suporte, resistencia

    if preco_atual > ema[-1] and rsi[-1] > 55 and histogram[-1] > histogram[-2] and direcao_m15 == "alta":
        prob = calcular_probabilidade(rsi, histogram)
        return "compra", prob, preco_atual, suporte, resistencia

    if preco_atual < ema[-1] and rsi[-1] < 45 and histogram[-1] < histogram[-2] and direcao_m15 == "baixa":
        prob = calcular_probabilidade(rsi, histogram)
        return "venda", prob, preco_atual, suporte, resistencia

    return "neutro", 0, preco_atual, suporte, resistencia

def main():
    global ultimo_sinal, ultimo_status, ultima_mensagem_status_id

    enviar_telegram("Iris Suprema M5 ativada. Elegância e precisão no controle do tempo gráfico.")

    while True:
        try:
            if pausado:
                time.sleep(10)
                continue

            url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}"
            url_m15 = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={CONFIRM_INTERVAL}&limit=30"
            candles = requests.get(url).json()
            candles_m15 = requests.get(url_m15).json()

            # Proteção contra dados inválidos
            if not candles or not all(len(c) >= 5 for c in candles):
                print("Erro: Dados de candles M5 inválidos.")
                time.sleep(60)
                continue

            if not candles_m15 or not all(len(c) >= 5 for c in candles_m15):
                print("Erro: Dados de candles M15 inválidos.")
                time.sleep(60)
                continue

            alerta_antecipado(candles)
            decisao, prob, preco_atual, suporte, resistencia = analisar_sinal(candles, candles_m15)
            acompanhar_resultado(preco_atual)
            considerar_reentrada(preco_atual)

            if time.time() - ultimo_status >= STATUS_INTERVAL and not ultimo_sinal["tipo"]:
                apagar_ultima_mensagem_status()
                status_id = enviar_telegram("30 minutos se passaram... Nenhum sinal foi identificado no M5. Continuamos atentos, refinando.")
                ultima_mensagem_status_id = status_id
                ultimo_status = time.time()

            if decisao in ["compra", "venda"] and ultimo_sinal["tipo"] is None:
                take = resistencia if decisao == "compra" else suporte
                stop = suporte if decisao == "compra" else resistencia

                msg = (
                    f"✨ *SINAL DE {decisao.upper()} – BTCUSD (M5)* ✨\n"
                    f"• Entrada: `{preco_atual:.2f}`\n"
                    f"• Take: `{take:.2f}`\n"
                    f"• Stop: `{stop:.2f}`\n"
                    f"• Probabilidade estimada: `{prob}%`"
                )
                enviar_telegram(msg)
                registrar_csv(decisao, preco_atual, take, stop, prob)
                ultimo_sinal = {"tipo": decisao, "preco": preco_atual, "take": take, "stop": stop}

            time.sleep(60)

        except Exception as e:
            print("Erro durante execução:", e)
            time.sleep(60)

main()
