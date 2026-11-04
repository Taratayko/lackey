"""No network required: tests cross-server protocol and Mongo decision semantics."""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from site_feedback_sync import SiteSyncClient, SiteSyncEngine, serialize_item, serialize_page

OID = 'a' * 24


class StubCursor:
    def __init__(self, docs):
        self.docs = docs
    def sort(self, *args):
        return self
    def limit(self, *args):
        return self
    async def to_list(self, n):
        return self.docs[:n]


class StubCollection:
    def __init__(self, rows):
        self.rows = rows
    def find(self, selector, projection=None):
        if '_id' in selector and '$gt' in selector['_id']:
            return StubCursor([r for r in self.rows if r['_id'] > selector['_id']['$gt']])
        return StubCursor(self.rows)


class StubUsers:
    def __init__(self, rows):
        self.rows = rows
    def find(self, *args):
        async def items():
            for r in self.rows:
                yield r
        return items()


class StubDb:
    def __init__(self, rows, users=()):
        self.site_feedback_items = StubCollection(rows)
        self.site_feedback_users = StubUsers(users)


class StubRepo:
    def __init__(self, rows=(), users=()):
        self.db = StubDb(list(rows), users)
        self.doc = rows[0] if rows else None
        self.changed = []
    async def pages(self, include_admin=False):
        return [{'_id': 'home', 'title': 'Главная', 'route': '/', 'access':'public'}]
    async def get_item(self, item_id):
        return self.doc if self.doc is not None and str(self.doc['_id']) == item_id else None
    async def change_status(self, item, status, actor, note, marker):
        if item['status'] != 'new':
            return False
        item['status'] = status
        item['history'].append({'sync_action_id': marker})
        self.changed.append(marker)
        return True


def item():
    return {'_id': OID, 'status': 'new', 'revision': 0, 'body': 'hello',
            'author': {'telegram_user_id': 123, 'username_snapshot': 'sample_user'},
            'created_at': datetime(2026, 10, 21, tzinfo=timezone.utc), 'history': []}


class DummyClient:
    def __init__(self, actions=()):
        self.calls = []
        self.remote_actions = actions
    async def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == 'actions':
            return {'actions': self.remote_actions}
        return {'ok': True}


class RemoteSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_plain_http_and_short_token(self):
        for url, token in [('http://site.example/api/site-feedback/sync/v1', 'x'*40),
                           ('https://site.example/api/site-feedback/sync/v1', 'x'),
                           ('https://user:pass@site.example/api/site-feedback/sync/v1', 'x'*40)]:
            with self.assertRaises(ValueError):
                SiteSyncClient(url, token, None)

    def test_valid_external_https_endpoint(self):
        client = SiteSyncClient('https://site.example/api/site-feedback/sync/v1', 'x'*40, None)
        self.assertTrue(client.base.startswith('https://'))

    def test_serialization_no_unsafe_fields(self):
        value = item()
        value['submission_key'] = 'do_not_export'
        exported = serialize_item(value)
        self.assertNotIn('submission_key', exported)
        self.assertEqual(exported['revision'], 0)
        self.assertIn('2026-10-21', exported['created_at'])
        self.assertEqual(serialize_page({'_id':'home','title':'Home','route':'/','access':'public'})['key'], 'home')

    async def test_snapshot_sends_pages_and_items(self):
        client = DummyClient()
        repo = StubRepo([item()])
        await SiteSyncEngine(repo, client).snapshot()
        calls = [p for _, name, p in client.calls if name == 'snapshot']
        self.assertEqual(calls[0]['items'][0]['id'], OID)
        self.assertEqual(calls[0]['pages'][0]['key'], 'home')

    async def test_snapshot_when_no_items(self):
        client = DummyClient()
        await SiteSyncEngine(StubRepo(), client).snapshot()
        self.assertEqual(client.calls[0][2]['pages'][0]['key'], 'home')
        self.assertEqual(client.calls[0][2]['items'], [])

    async def test_old_usernames_synced(self):
        repo = StubRepo(users=[{'_id':123,'seen_usernames':['old_user','new_user']}])
        client = DummyClient()
        await SiteSyncEngine(repo, client).snapshot()
        aliases = [p['aliases'] for _, name, p in client.calls if name == 'snapshot' and p['aliases']]
        self.assertEqual(len(aliases[0]), 2)

    async def test_action_updates_once(self):
        action = {'id':71,'feedback_id':OID,'expected_status':'new',
                  'target_status':'in_review','note':'ok','actor':'moderator'}
        repo = StubRepo([item()]); client = DummyClient([action])
        notify = AsyncMock()
        eng = SiteSyncEngine(repo, client, notify)
        await eng.actions()
        await eng.actions()  # ack lost: site re-delivered identical action
        acks = [p for _, k, p in client.calls if k == 'ack']
        self.assertEqual([p['state'] for p in acks], ['applied','applied'])
        self.assertEqual(repo.changed, ['site:71'])
        notify.assert_awaited_once()

    async def test_stale_expected_status_is_conflict(self):
        row = item(); row['status'] = 'accepted'; repo = StubRepo([row])
        client = DummyClient([{'id':1,'feedback_id':OID,'expected_status':'new',
                              'target_status':'in_review','note':'ok','actor':'admin'}])
        await SiteSyncEngine(repo, client).actions()
        ack = [p for _, k, p in client.calls if k == 'ack'][0]
        self.assertEqual(ack['state'], 'conflict')
        self.assertFalse(repo.changed)

    async def test_missing_item_conflict(self):
        repo = StubRepo(); client = DummyClient()
        state, reason = await SiteSyncEngine(repo, client).apply_action({'id':1,'feedback_id':OID})
        self.assertEqual((state,reason), ('conflict','item_missing'))

    async def test_network_failure_does_not_advance_actions(self):
        repo = StubRepo([item()])
        client = DummyClient([{'id':3,'feedback_id':OID,'expected_status':'new',
                              'target_status':'accepted','note':'ok','actor':'admin'}])
        async def fail_ack(method, path, payload=None):
            if path == 'ack': raise OSError('temporary connection lost')
            return {'actions': client.remote_actions}
        client.call = fail_ack
        with self.assertRaises(OSError):
            await SiteSyncEngine(repo,client).actions()
        self.assertEqual(repo.changed, ['site:3'])


if __name__ == '__main__':
    unittest.main()
