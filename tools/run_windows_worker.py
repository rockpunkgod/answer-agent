"""Dedicated node bootstrap. Unconnected native capabilities stay unavailable.

There is deliberately no free Shell, script import, credential file or recipient
parameter. Production native handlers must be integrated and reviewed in code;
the bootstrap cannot invent a healthy UI or silently enable external actions.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import time

from helpdesk.worker_client import HTTPBusinessClient, WorkerClient
from helpdesk.worker_contracts import WorkerHealth, utc_now
from helpdesk.worker_runtime import WorkerRuntime


def unavailable_health(worker_id,account_id,profile_id=None):
    return WorkerHealth(worker_id,account_id,True,False,False,'UNKNOWN',profile_id,None,None,
                        utc_now().isoformat(),False)


def native_handler_unavailable(command,guard):
    return guard.result('FAILED_BEFORE_ACTION','TRUSTED_NATIVE_ADAPTER_NOT_CONNECTED')


def main(argv=None):
    parser=argparse.ArgumentParser(description='专用Windows节点：业务API与本地恢复缓存，未接通能力保持暂停')
    parser.add_argument('--server',default='http://127.0.0.1:8767')
    parser.add_argument('--worker-id',required=True)
    parser.add_argument('--account-id',required=True,help='已由管理员配置的账号引用；无需密码')
    parser.add_argument('--local-root',type=Path,default=Path('data/private/dedicated-worker'))
    parser.add_argument('--resource-root',type=Path,default=Path(__file__).resolve().parents[1],help='该交互桌面的共同锁与Profile目录根')
    parser.add_argument('--token-env',default='HELPDESK_WORKER_TOKEN')
    parser.add_argument('--interval',type=int,default=5)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--recover-only',action='store_true',help='仅回传已有执行证据；不触发桌面动作')
    parser.add_argument('--prepare-profile',help='创建空的专用Profile引用；不启动浏览器或复制登录')
    parser.add_argument('--native-metadata',action='store_true',help='通过固定Windows-MCP探针核对桌面；没有应用身份检测器仍不允许执行GUI')
    args=parser.parse_args(argv)
    if not 1<=args.interval<=60:parser.error('interval必须为1—60秒')
    api=HTTPBusinessClient(args.server,token_env=args.token_env)
    runtime=WorkerRuntime(args.resource_root)
    if args.prepare_profile:
        from helpdesk.worker_contracts import reference
        profile=reference(args.prepare_profile)
        runtime.profiles.register(profile,args.account_id,runtime.profiles.root/profile)
    with ExitStack() as owned:
        health_provider=lambda:unavailable_health(args.worker_id,args.account_id,args.prepare_profile)
        if args.native_metadata and not args.recover_only:
            from helpdesk.worker_native_health import WindowsMCPHealthProvider, metadata_reads_allowed
            try:
                permitted=metadata_reads_allowed(api.worker_state(args.worker_id),args.worker_id)
            except Exception:permitted=False
            if not permitted:
                print(json.dumps({'state':'DESKTOP_READS_NOT_AUTHORIZED','native_adapter_connected':False,
                                  'native_metadata_connected':False,'gui_ready_verified':False}),flush=True)
                return 0
            from helpdesk.mcp_transport import MCPProcess
            try:
                mcp=owned.enter_context(MCPProcess(Path(__file__).resolve().parents[1]/'.venv-windows-mcp/Scripts/python.exe'))
            except Exception:
                print(json.dumps({'state':'NATIVE_METADATA_UNAVAILABLE','native_adapter_connected':False,
                                  'native_metadata_connected':False,'gui_ready_verified':False}),flush=True)
                return 0
            health_provider=WindowsMCPHealthProvider(mcp,api,worker_id=args.worker_id,account_id=args.account_id)
        client=WorkerClient(api,args.worker_id,args.local_root/'execution-cache.db',runtime=runtime,
            health_provider=health_provider,handler=native_handler_unavailable)
        last=None
        try:
            while True:
                value={'state':'RECOVERY_RETURNED' if client.recover_pending() else 'PENDING_RECOVERY'} if args.recover_only else client.run_once()
                value=dict(value,native_adapter_connected=False,gui_ready_verified=False,
                           native_metadata_connected=bool(getattr(health_provider,'native_metadata_connected',False)))
                if value!=last:
                    print(json.dumps(value,ensure_ascii=False),flush=True)
                    last=value
                if args.once or args.recover_only:break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(json.dumps({'state':'NODE_STOPPED','queued_results_preserved':True}),flush=True)
        finally:client.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
