"""Existing collector supervisor bridge to the isolated collection-only CLI."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


class PersonalWeChatPollFailed(RuntimeError):
    """Stop collection; no retry or business dispatch after an uncertain poll."""


class PersonalWeChatPoller:
    def __init__(self,config):
        settings=config['collector'];source=config['source']
        self.config_path=Path(source['replica_config']).resolve(strict=True)
        self.config_bytes=self.config_path.read_bytes()
        private_config=json.loads(self.config_bytes.decode('utf-8-sig'))
        private=Path(private_config['private_root']).resolve(strict=True)
        if (not private.is_relative_to(ROOT/'data/private')
                or not self.config_path.is_relative_to(private)
                or private_config.get('prototype_only') is not True
                or private_config.get('groups_reviewed') is not True):
            raise ValueError('CONFIRMED_PRIVATE_COLLECTION_CONFIG_REQUIRED')
        self.database=Path(private_config['collector_database']).resolve()
        if (not self.database.is_relative_to(private)
                or self.database!=Path(settings['database']).resolve()):
            raise ValueError('SHARED_COLLECTOR_DATABASE_MUST_MATCH')
        if settings.get('unread_wakeup',{}).get('enabled'):
            raise ValueError('PERSONAL_COLLECTION_HAS_NO_DESKTOP_WAKEUP')
        self.python=private/'venv/Scripts/python.exe'
        if not self.python.is_file():raise ValueError('ISOLATED_COLLECTOR_PYTHON_REQUIRED')
        self.timeout=source.get('poll_timeout_seconds',120)
        if type(self.timeout) is not int or not 1<=self.timeout<=600:
            raise ValueError('INVALID_COLLECTION_TIMEOUT')

    def poll(self):
        try:
            if self.config_path.read_bytes()!=self.config_bytes:
                raise PersonalWeChatPollFailed('COLLECTION_CONFIG_CHANGED_RESTART_REQUIRED')
            result=subprocess.run([str(self.python),'-X','utf8','-B',
                str(ROOT/'tools/personal_wechat_collect.py'),'poll','--config',str(self.config_path)],
                capture_output=True,text=True,encoding='utf-8',timeout=self.timeout)
            if result.returncode:raise PersonalWeChatPollFailed('COLLECTION_CHILD_FAILED')
            value=json.loads(result.stdout)
            if (value.get('status')!='RAW_COLLECTOR_SYNCED' or value.get('mode')!='LIVE'
                    or value.get('desktop_access') is not False or value.get('sending_enabled') is not False
                    or any(type(value.get(key)) is not int or value[key]<0 for key in ('inserted','duplicates'))):
                raise PersonalWeChatPollFailed('COLLECTION_RESULT_INVALID')
        except (subprocess.TimeoutExpired,OSError,ValueError,AttributeError):
            raise PersonalWeChatPollFailed('COLLECTION_UNAVAILABLE') from None
        return {'sync':{'inserted_count':value['inserted'],'duplicate_count':value['duplicates'],
                        'conflict_count':0,'has_more':False},
                'events_processed':0,'drain_enabled':False,'collection_only':True,
                'student_send_enabled':False}


def run_personal_collection(store,source,settings):
    if Path(store.path).resolve()!=source.database:
        raise PersonalWeChatPollFailed('COLLECTOR_DATABASE_CHANGED')
    return source.poll()
