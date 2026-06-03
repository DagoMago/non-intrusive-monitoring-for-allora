import os
import json
import time
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient
import paho.mqtt.client as mqtt


# ============================================================
# CONFIGURACIÓN INFLUXDB
# ============================================================

INFLUX_URL = os.getenv("INFLUX_URL", "http://127.0.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "allora-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "allora-bucket")

MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "node_metrics")
MQTT_TOPIC_COLUMN = os.getenv("MQTT_TOPIC_COLUMN", "mqtt_topic")
UPTIME_FIELD = os.getenv("UPTIME_FIELD", "Uptime")


# ============================================================
# NODOS A CONTROLAR
# ============================================================

NODES = [
    {
        "mac": "62d92158",
        "metrics_topic": "allora/gateway_01/62d92158/metrics",
        "control_topic": "allora/gateway_01/62d92158/control",
    },
    {
        "mac": "eb0fb4e5",
        "metrics_topic": "allora/gateway_01/eb0fb4e5/metrics",
        "control_topic": "allora/gateway_01/eb0fb4e5/control",
    },
]


# ============================================================
# CONFIGURACIÓN MQTT WEBSOCKET
# ============================================================

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "9001"))

# Según tu broker puede ser "/" o "/mqtt"
MQTT_WS_PATH = os.getenv("MQTT_WS_PATH", "/")

MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")


# ============================================================
# REGLAS DE CONTROL
# ============================================================

LOOP_PERIOD_SECONDS = 10

# 10 valores consecutivos iguales de Uptime -> S-RESET
UPTIME_REPEAT_N = 10

# 1 minuto sin recibir nada -> H-RESET
NO_DATA_SECONDS = 60

SOFT_RESET_COOLDOWN_SECONDS = 180
HARD_RESET_COOLDOWN_SECONDS = 180

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


# ============================================================
# ESTADO INTERNO
# ============================================================

last_action_time = {}


def now_utc():
    return datetime.now(timezone.utc)


def seconds_since(dt):
    return (now_utc() - dt).total_seconds()


def can_send(mac, command_type, cooldown):
    key = f"{mac}:{command_type}"
    current_time = time.time()
    last = last_action_time.get(key, 0)

    if current_time - last < cooldown:
        return False

    last_action_time[key] = current_time
    return True


def publish_command(mqtt_client, node, command_type):
    payload = {
        "type": command_type,
        "controlled_node_mac": node["mac"],
    }

    payload_str = json.dumps(payload)

    if DRY_RUN:
        print(
            f"[DRY-RUN] publish topic={node['control_topic']} "
            f"payload={payload_str}"
        )
        return

    result = mqtt_client.publish(
        node["control_topic"],
        payload_str,
        qos=1,
        retain=False,
    )

    result.wait_for_publish(timeout=5)

    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        print(
            f"[MQTT] Sent {command_type} to {node['mac']} "
            f"on {node['control_topic']}: {payload_str}"
        )
    else:
        print(
            f"[MQTT] ERROR sending {command_type} to {node['mac']}. "
            f"rc={result.rc}"
        )


def query_last_uptime_values(query_api, node, n):
    query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => r["_measurement"] == "{MEASUREMENT}")
  |> filter(fn: (r) => r["{MQTT_TOPIC_COLUMN}"] == "{node["metrics_topic"]}")
  |> filter(fn: (r) => r["_field"] == "{UPTIME_FIELD}")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {n})
'''

    tables = query_api.query(query, org=INFLUX_ORG)

    values = []
    for table in tables:
        for record in table.records:
            values.append(record.get_value())

    return values


def query_last_any_record_time(query_api, node):
    query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -10m)
  |> filter(fn: (r) => r["_measurement"] == "{MEASUREMENT}")
  |> filter(fn: (r) => r["{MQTT_TOPIC_COLUMN}"] == "{node["metrics_topic"]}")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
'''

    tables = query_api.query(query, org=INFLUX_ORG)

    for table in tables:
        for record in table.records:
            return record.get_time()

    return None


def uptime_is_stuck(values):
    if len(values) < UPTIME_REPEAT_N:
        return False

    first = values[0]
    return all(value == first for value in values)


def check_node(query_api, mqtt_client, node):
    mac = node["mac"]

    print(f"\n[CHECK] Node {mac}")
    print(f"[INFO] Metrics topic: {node['metrics_topic']}")
    print(f"[INFO] Control topic: {node['control_topic']}")

    # ------------------------------------------------------------
    # Regla 1: 1 minuto sin recibir nada -> HARD-REBOOT
    # ------------------------------------------------------------

    last_time = query_last_any_record_time(query_api, node)

    if last_time is None:
        print(f"[WARN] No data found for node {mac}")

        if can_send(mac, "HARD-REBOOT", HARD_RESET_COOLDOWN_SECONDS):
            publish_command(mqtt_client, node, "HARD-REBOOT")
        else:
            print(f"[SKIP] HARD-REBOOT cooldown active for {mac}")

        return

    silence = seconds_since(last_time)

    print(f"[INFO] Last data from {mac}: {silence:.1f} seconds ago")

    if silence >= NO_DATA_SECONDS:
        print(f"[ALERT] Node {mac} silent for {silence:.1f}s -> HARD-REBOOT")

        if can_send(mac, "HARD-REBOOT", HARD_RESET_COOLDOWN_SECONDS):
            publish_command(mqtt_client, node, "HARD-REBOOT")
        else:
            print(f"[SKIP] HARD-REBOOT cooldown active for {mac}")

        return

    # ------------------------------------------------------------
    # Regla 2: 10 valores iguales de Uptime -> RESET
    # ------------------------------------------------------------

    uptime_values = query_last_uptime_values(query_api, node, UPTIME_REPEAT_N)

    print(f"[INFO] Last Uptime values from {mac}: {uptime_values}")

    if uptime_is_stuck(uptime_values):
        print(f"[ALERT] Uptime stuck in node {mac} -> RESET")

        if can_send(mac, "RESET", SOFT_RESET_COOLDOWN_SECONDS):
            publish_command(mqtt_client, node, "RESET")
        else:
            print(f"[SKIP] RESET cooldown active for {mac}")


def create_mqtt_client():
    client = mqtt.Client(
        client_id="allora-auto-watchdog",
        transport="websockets",
    )

    client.ws_set_options(path=MQTT_WS_PATH)

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    return client


def main():
    if not INFLUX_TOKEN:
        raise RuntimeError(
            "INFLUX_TOKEN no está definido. "
            "Ejecuta: export INFLUX_TOKEN='tu_token'"
        )

    print("[START] AlLoRa automatic watchdog")
    print(f"[MODE] DRY_RUN={DRY_RUN}")

    influx_client = InfluxDBClient(
        url=INFLUX_URL,
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
    )

    query_api = influx_client.query_api()
    mqtt_client = create_mqtt_client()

    try:
        while True:
            for node in NODES:
                try:
                    check_node(query_api, mqtt_client, node)
                except Exception as e:
                    print(f"[ERROR] Node {node['mac']}: {e}")

            time.sleep(LOOP_PERIOD_SECONDS)

    except KeyboardInterrupt:
        print("[STOP] Manual stop requested")

    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        influx_client.close()
        print("[STOP] Watchdog stopped")


if __name__ == "__main__":
    main()