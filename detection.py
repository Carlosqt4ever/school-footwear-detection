"""Camera and model lifecycle, shared by all authenticated viewers."""
import threading
import time
from collections import deque


def open_camera(cv2, index, stop_event):
    """Try Windows camera backends without forcing an unsupported resolution."""
    import os
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == 'nt' else [cv2.CAP_ANY]
    for backend in backends:
        if stop_event.is_set():
            return None
        cap = cv2.VideoCapture(index, backend)
        try:
            if cap.isOpened():
                for _ in range(8):
                    if stop_event.is_set():
                        break
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        return cap
                    time.sleep(0.1)
        except Exception:
            cap.release()
            raise
        cap.release()
    if stop_event.is_set():
        return None
    raise RuntimeError(
        f'Cannot read webcam {index}. Tried all supported camera connection methods. '
        'Test the Windows Camera app, close other camera apps, enable Camera access '
        'and Let desktop apps access your camera in Windows Settings, then check '
        'the camera index in this app (usually 0 or 1). Also check the camera privacy shutter or keyboard switch.'
    )


class StabilityTracker:
    def __init__(self, frames=5, cooldown=12):
        self.votes = deque(maxlen=frames)
        self.cooldown = cooldown
        self.last_recorded = float('-inf')
        self.armed = True
        self.empty_since = None

    def update(self, label, now):
        if label == 'No footwear detected':
            self.votes.clear()
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= 2:
                self.armed = True
            return False
        self.empty_since = None
        if label not in ('valid', 'not_valid'):
            self.votes.clear()
            return False
        self.votes.append(label)
        stable = len(self.votes) == self.votes.maxlen and len(set(self.votes)) == 1
        if stable and self.armed and now - self.last_recorded >= self.cooldown:
            self.last_recorded = now
            self.armed = False
            return True
        return False


class Detector:
    def __init__(self, model_path, settings, record):
        self.model_path = model_path
        self.settings = settings
        self.record = record
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.frame = None
        self.state = {'running': False, 'label': 'Camera stopped', 'confidence': 0, 'fps': 0, 'error': None}

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.frame = None
            self.state.update(running=True, label='Loading model', error=None, fps=0, confidence=0)
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.state['running']:
                self.state.update(label='Stopping camera')

    def _run(self):
        cap = None
        try:
            import cv2
            from ultralytics import YOLO
            config = self.settings()
            model = YOLO(str(self.model_path))
            if self.stop_event.is_set():
                return
            with self.lock:
                self.state.update(label='Opening webcam')
            cap = open_camera(cv2, int(config['camera_index']), self.stop_event)
            if cap is None:
                return
            tracker = StabilityTracker()
            while not self.stop_event.is_set():
                started = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('The webcam stopped responding. Check the connection and restart the camera.')
                result = model.predict(frame, conf=0.1, imgsz=640, verbose=False)[0]
                candidates = []
                for box in result.boxes:
                    name = result.names[int(box.cls.item())]
                    if name not in ('valid', 'not_valid'):
                        continue
                    confidence = float(box.conf.item())
                    candidates.append((name, confidence))
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    color = (49, 176, 94) if name == 'valid' else (71, 76, 229)
                    if confidence < config['confidence_threshold']:
                        color = (40, 180, 235)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f'{name.replace("_", " ")} {confidence:.0%}', (x1, max(24, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                accepted = [(name, score) for name, score in candidates if score >= config['confidence_threshold']]
                if not candidates:
                    label, confidence = 'No footwear detected', 0
                elif not accepted or len({name for name, _ in accepted}) > 1:
                    label, confidence = 'Uncertain', max(score for _, score in candidates)
                else:
                    label, confidence = max(accepted, key=lambda item: item[1])
                if tracker.update(label, time.monotonic()):
                    snapshot = None
                    if config['save_images']:
                        ok, encoded = cv2.imencode('.jpg', frame)
                        if ok:
                            snapshot = encoded.tobytes()
                    self.record(label, confidence, snapshot)
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                with self.lock:
                    if ok:
                        self.frame = encoded.tobytes()
                    self.state.update(label=label, confidence=round(confidence * 100, 1), fps=round(1 / max(time.monotonic() - started, 0.001), 1))
        except Exception as exc:
            with self.lock:
                self.state.update(error=str(exc), label='Camera unavailable')
        finally:
            if cap is not None:
                cap.release()
            with self.lock:
                self.frame = None
                self.state.update(running=False, fps=0, confidence=0)
                if not self.state['error']:
                    self.state['label'] = 'Camera stopped'

    def stream(self):
        while True:
            with self.lock:
                frame = self.frame
                running = self.state['running']
            if not running:
                return
            if frame:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
            time.sleep(0.08)
