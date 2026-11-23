"""Real HTTP checks of chat approval and local write boundaries, no game calls."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import server


class ChatServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [patch.object(server, 'LOG_PATH', Path(self.tmp.name) / 'events.jsonl'),
                        patch.object(server, 'STATE_PATH', Path(self.tmp.name) / 'state.json'),
                        patch.object(server, 'state', dict(copy.deepcopy(server.state), feed=[]))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.url = 'http://127.0.0.1:%d' % self.http.server_port

    def post(self, body, headers=None):
        req = urllib.request.Request(self.url + '/event', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_external_approval_cannot_be_forged(self):
        code, _ = self.post(dict(kind='chat', name='viewer', text='hello',
                                 approved=True, source='twitch'))
        self.assertEqual(code, 403)
        self.assertEqual(server.state['feed'], [])

    def test_internal_approval_retains_provenance_and_scrubs_private_name(self):
        server.publish_screened_chat(dict(kind='chat', name='viewer',
                                          text='Hi M', channel='test'))
        item = server.state['feed'][0]
        self.assertEqual((item['name'], item['text']), ('viewer', 'Hi M'))
        self.assertIs(item['approved'], True)
        self.assertEqual(item['source'], 'twitch')

    def test_human_still_works_and_cross_origin_cannot_forge_it(self):
        body = dict(kind='human', text='hello')
        self.assertEqual(self.post(body, {'Origin': 'https://untrusted.example'})[0], 403)
        self.assertEqual(self.post(body, {'Host': 'untrusted.example'})[0], 403)
        self.assertEqual(self.post(body, {'Origin': self.url})[0], 200)
        self.assertNotIn('approved', server.state['feed'][0])


if __name__ == '__main__':
    unittest.main()
