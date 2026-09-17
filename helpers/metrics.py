"""In-memory application metrics collector."""

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class MetricsCollector:
    def __init__(self):
        self.counters = {}
        self.gauges = {}
        self._lock = threading.Lock()

    def increment(self, counter_name: str, value: int = 1):
        with self._lock:
            if counter_name not in self.counters:
                self.counters[counter_name] = 0
            self.counters[counter_name] += value

    def set_gauge(self, gauge_name: str, value: float):
        with self._lock:
            self.gauges[gauge_name] = value

    def get_all(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges)
            }

    def get_summary(self) -> str:
        with self._lock:
            lines = []
            if self.counters:
                lines.append("Counters:")
                for k, v in self.counters.items():
                    lines.append(f"  - {k}: {v}")
            if self.gauges:
                lines.append("Gauges:")
                for k, v in self.gauges.items():
                    lines.append(f"  - {k}: {v}")
            return "\n".join(lines) if lines else "No metrics collected yet."

# Global singleton
metrics = MetricsCollector()

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; version=0.0.4')
            self.end_headers()
            
            lines = []
            data = metrics.get_all()
            for k, v in data['counters'].items():
                lines.append(f"# TYPE tubetapper_{k} counter\ntubetapper_{k} {v}")
            for k, v in data['gauges'].items():
                lines.append(f"# TYPE tubetapper_{k} gauge\ntubetapper_{k} {v}")
            
            response = "\\n".join(lines) + "\\n"
            self.wfile.write(response.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logging to avoid noise

def start_metrics_server(port=9090):
    server = HTTPServer(('0.0.0.0', port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

