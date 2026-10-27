import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from app import create_app
from detection import StabilityTracker

class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app({'TESTING': True, 'STORAGE': self.temp.name})
        self.client = self.app.test_client()
    def tearDown(self):
        self.temp.cleanup()
    def post(self, path, data=None, client=None):
        client = client or self.client
        client.get('/login')
        with client.session_transaction() as s:
            token = s['csrf']
        return client.post(path, data=dict(data or {}, csrf=token))
    def admin(self):
        self.post('/setup', {'username':'admin','password':'safe-password-123'})
        self.post('/login', {'username':'admin','password':'safe-password-123'})
    def insert(self, label='valid', image=None, date=None):
        with closing(sqlite3.connect(self.root / 'footwear.sqlite')) as db, db:
            return db.execute('INSERT INTO detections(created_at,label,confidence,image) VALUES (?,?,?,?)', (date or datetime.now().isoformat(timespec='seconds'),label,.92,image)).lastrowid
    def test_authentication_and_csrf(self):
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.get('/api/status').status_code,401)
        self.assertEqual(self.client.post('/setup',data={'username':'bad'}).status_code,400)
        self.admin()
        for path in ('/','/live','/history','/users','/settings','/account'):
            response = self.client.get(path)
            self.assertEqual(response.status_code,200,path)
            self.assertEqual(response.headers['X-Frame-Options'],'DENY')
        self.assertEqual(self.client.get('/setup').status_code,302)
        with closing(sqlite3.connect(self.root / 'footwear.sqlite')) as db, db:
            self.assertNotEqual(db.execute('SELECT password FROM users').fetchone()[0],'safe-password-123')
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code,302)
    def test_staff_permissions(self):
        self.admin()
        self.post('/users',{'username':'staff','password':'safe-password-456','role':'staff'})
        staff = self.app.test_client()
        self.post('/login',{'username':'staff','password':'safe-password-456'},staff)
        self.assertEqual(staff.get('/live').status_code,200)
        self.assertEqual(staff.get('/settings').status_code,403)
        self.assertEqual(staff.get('/users').status_code,403)
        self.assertEqual(self.post('/users',{'username':'evil','password':'safe-password-123','role':'admin'},staff).status_code,403)
    def test_review_filter_export(self):
        self.admin()
        record = self.insert()
        self.insert('not_valid')
        self.assertIn(b'92%',self.client.get('/history').data)
        self.post(f'/history/{record}/review',{'decision':'not_valid'})
        with closing(sqlite3.connect(self.root / 'footwear.sqlite')) as db, db:
            self.assertEqual(db.execute('SELECT label,review,reviewer FROM detections WHERE id=?',(record,)).fetchone(),('valid','not_valid','admin'))
        exported = self.client.get('/history/export?label=valid').data.decode()
        self.assertEqual(len(exported.splitlines()),2)
        self.assertIn('not_valid,admin',exported)
        self.assertEqual(self.client.get('/history?date=oops').status_code,400)
        self.assertEqual(self.post('/history/999/review',{'decision':'valid'}).status_code,404)
        self.assertEqual(self.post(f'/history/{record}/review',{'decision':'unknown'}).status_code,400)
    def test_settings_and_retention(self):
        self.admin()
        self.post('/settings',{'camera_index':'0','confidence_threshold':'70','retention_days':'2','save_images':'on'})
        self.assertEqual(self.app.extensions['detector'].settings()['confidence_threshold'],.7)
        self.post('/settings',{'camera_index':'-1','confidence_threshold':'nan','retention_days':'0'})
        self.assertEqual(self.app.extensions['detector'].settings()['confidence_threshold'],.7)
        for name in ('old.jpg','new.jpg'):
            (self.root / 'snapshots' / name).write_bytes(b'image')
        self.insert(image='old.jpg',date=(datetime.now()-timedelta(days=4)).isoformat())
        self.insert(image='new.jpg')
        self.client.get('/history')
        self.assertFalse((self.root / 'snapshots' / 'old.jpg').exists())
        response = self.client.get('/snapshots/new.jpg')
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertEqual(self.app.test_client().get('/snapshots/new.jpg').status_code,302)
    def test_camera_api(self):
        self.admin()
        with patch.object(self.app.extensions['detector'],'start') as start:
            self.assertEqual(self.post('/api/camera/start').status_code,200)
            start.assert_called_once()
        self.assertEqual(self.post('/api/camera/missing').status_code,404)
    def test_password_and_throttle(self):
        self.admin()
        self.post('/account',{'current_password':'safe-password-123','password':'new-password-456'})
        self.post('/logout')
        self.post('/login',{'username':'admin','password':'new-password-456'})
        self.assertEqual(self.client.get('/').status_code,200)
        self.post('/logout')
        for _ in range(8):
            self.post('/login',{'username':'admin','password':'wrong'})
        self.assertEqual(self.post('/login',{'username':'admin','password':'wrong'}).status_code,429)

class StabilityTests(unittest.TestCase):
    def test_duplicate_suppression(self):
        tracker = StabilityTracker(frames=3,cooldown=12)
        self.assertFalse(tracker.update('valid',0))
        self.assertFalse(tracker.update('valid',1))
        self.assertTrue(tracker.update('valid',2))
        for second in range(3,20):
            self.assertFalse(tracker.update('valid',second))
        tracker.update('No footwear detected',20)
        tracker.update('No footwear detected',22)
        tracker.update('not_valid',23)
        tracker.update('not_valid',24)
        self.assertTrue(tracker.update('not_valid',25))
    def test_uncertainty_and_flicker(self):
        tracker = StabilityTracker(frames=3)
        for second,label in enumerate(['valid','valid','Uncertain','valid','not_valid','valid','No footwear detected']):
            self.assertFalse(tracker.update(label,second))


class CameraConnectionTests(unittest.TestCase):
    def test_fallback_releases_failed_camera(self):
        import threading
        from unittest.mock import MagicMock
        from detection import open_camera
        cv = MagicMock()
        failed, working = MagicMock(), MagicMock()
        failed.isOpened.return_value = False
        working.isOpened.return_value = True
        working.read.return_value = (True, object())
        cv.VideoCapture.side_effect = [failed, working]
        with patch('os.name', 'nt'):
            self.assertIs(open_camera(cv, 0, threading.Event()), working)
        failed.release.assert_called_once()
        working.release.assert_not_called()
        self.assertEqual(cv.VideoCapture.call_count, 2)

    def test_no_camera_produces_actionable_error(self):
        import threading
        from unittest.mock import MagicMock
        from detection import open_camera
        cv = MagicMock()
        cv.VideoCapture.return_value.isOpened.return_value = False
        with patch('os.name', 'nt'):
            with self.assertRaisesRegex(RuntimeError, 'Cannot read webcam 1'):
                open_camera(cv, 1, threading.Event())
        self.assertEqual(cv.VideoCapture.return_value.release.call_count, 3)

if __name__ == '__main__':
    unittest.main()

