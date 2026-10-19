"""Restricted stdio WeCom MCP. Uses an existing database; sends are off by default."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helpdesk.wecom_tools import WeComTools


def build_server(service):
    from mcp.server.mcpserver import MCPServer
    mcp=MCPServer('answer-agent-wecom',instructions=(
        'Only approved WeCom business operations. No arbitrary recipient, coordinates, commands or file paths. '
        'UNAVAILABLE is not an empty chat; INVOKED_UNVERIFIED is not verified delivery.'))

    @mcp.tool()
    def wecom_inspect_controls() -> dict:
        """Inspect actual UIA availability and record required workflow controls; never clicks."""
        return service.call('inspect_controls')

    @mcp.tool()
    def wecom_list_chats() -> dict:
        """List only the currently verified approved chat; does not claim a full chat inventory."""
        return service.call('list_chats')

    @mcp.tool()
    def wecom_open_chat(chat_id: str) -> dict:
        """Verify an approved chat is already open; stops if navigation is not reviewed."""
        return service.call('open_chat',chat_id=chat_id)

    @mcp.tool()
    def wecom_get_messages(chat_id: str) -> dict:
        """Copy reviewed visible text records; never substitutes collection time for sent time."""
        return service.call('get_messages',chat_id=chat_id)

    @mcp.tool()
    def wecom_open_image(message_id: str) -> dict:
        """Open one locally reviewed incoming image after current layout verification."""
        return service.call('open_image',message_id=message_id)

    @mcp.tool()
    def wecom_prepare_reply(outbox_id: str) -> dict:
        """Stage an existing eligible Outbox body and read it back. Never sends or approves."""
        return service.call('prepare_reply',outbox_id=outbox_id)

    @mcp.tool()
    def wecom_send_reply(outbox_id: str) -> dict:
        """Dispatch a prepared eligible reply through original Workflow/Outbox and receipt checks."""
        return service.call('send_reply',outbox_id=outbox_id)

    @mcp.tool()
    def wecom_get_chat_state() -> dict:
        """Verify current native window, approved chat identity, layout and editor state."""
        return service.call('get_chat_state')

    @mcp.tool()
    def wecom_download_attachment(message_id: str) -> dict:
        """Verify an already saved original attachment; no arbitrary download path or new Save As."""
        return service.call('download_attachment',message_id=message_id)

    return mcp


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--session',type=Path,help='Locally reviewed private recipe, never supplied by a model tool call')
    p.add_argument('--enable-input',action='store_true')
    p.add_argument('--enable-send',action='store_true')
    args=p.parse_args()
    if args.enable_send and not args.enable_input:p.error('--enable-send requires --enable-input')
    session=json.loads(args.session.read_text(encoding='utf-8-sig')) if args.session else None
    service=WeComTools(args.database,session=session,allow_input=args.enable_input,allow_send=args.enable_send)
    build_server(service).run(transport='stdio')


if __name__=='__main__':main()
