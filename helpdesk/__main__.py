import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tomllib

from .adapters import MockDeepSeek, require_simulation_config
from .domain import Intent, Option, Question
from .service import Helpdesk, Incoming
from .storage import Store, encode


CONTENTS = ("To visit a friend.", "To find a new job.", "To continue his studies.", "To look after his mother.")


def demo_question(number="12", stem="Why did he return home?"):
    return Question(number, stem, stem, tuple(Option.confirmed(label, text, i, "demo-fixture")
                    for i, (label, text) in enumerate(zip("ABCD", CONTENTS))), "demo-fixture")


def demo(service):
    # Stable demo event IDs allow safe reruns without manufacturing extra messages.
    bid = service.bind("MOCK_GROUP", "MOCK_STUDENT", "演示学生", verified=True)
    first = service.ingest(Incoming(bid, "请讲第12题", Intent.NEW, platform_id="demo-1",
                                   verified_question=demo_question(), raw_material="Demo passage.", verified_material="Demo passage."))
    if first.status == "DUPLICATE":
        return {"simulation": True, "note": "演示已经运行，请使用新的演示数据库重跑。", "health": service.health()}
    context = service.context(first.turn_id)
    generated = MockDeepSeek().generate(context)
    aid, state = service.record_simulated_answer(context, generated.text)
    follow = service.ingest(Incoming(bid, "为什么不选B？", Intent.FOLLOWUP, platform_id="demo-2", quote_message_id=first.message_id))
    follow_context = service.context(follow.turn_id)
    corrected = service.ingest(Incoming(bid, "刚才拍错了，这张才对。", Intent.CORRECTION, platform_id="demo-3",
                                       quote_message_id=first.message_id, verified_question=demo_question(stem="Why did he NOT return home?")))
    _, late_state = service.record_simulated_answer(follow_context, "模拟的旧版迟到结果")
    return {"simulation": True, "first_case": first.case_id, "first_answer": {"id": aid, "initial_state": state},
            "followup_student_B": follow_context["student_question"]["options"][1]["verified_text"],
            "correction_question": corrected.question_id, "late_answer_state": late_state,
            "note": "未调用模型、浏览器或企业微信；所有Outbox记录均未发送。", "health": service.health()}


def main():
    parser = argparse.ArgumentParser(description="本地模拟答疑工程，无真实外发能力")
    parser.add_argument("command", choices=("init", "demo", "health", "review", "confirm", "doctor", "serve", "recover"))
    parser.add_argument("--db", default="data/helpdesk.db")
    parser.add_argument("--config")
    parser.add_argument("--real-config", help="Prepared workbench JSON, read-only doctor inspection")
    parser.add_argument("--message-id")
    parser.add_argument("--question-id")
    parser.add_argument("--input", help="Local reviewer JSON: question plus optional verified_material")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.real_config and args.command != 'doctor':
        parser.error('--real-config here is supported only by doctor; start the real workbench with helpdesk.demo_server')
    config = {}
    if args.config:
        with open(args.config, "rb") as stream:
            config = tomllib.load(stream)
    require_simulation_config(config)
    if args.command == "doctor":
        from .diagnostics import doctor
        print(encode(doctor(args.config, real_config_path=args.real_config)))
        return
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        from .demo_server import DemoHTTPServer
        server = DemoHTTPServer(("127.0.0.1", args.port), path)
        print(f"模拟演示：http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    store = Store(path)
    try:
        service = Helpdesk(store)
        if args.command == "demo":
            output = demo(service)
        elif args.command == "recover":
            from .workflow import Workflow
            output = Workflow(store).recover()
        elif args.command == "review":
            output = [dict(r) for r in store.all("SELECT h.id,h.message_id,h.reason,m.case_id,m.question_id FROM human_tasks h JOIN messages m ON m.id=h.message_id WHERE h.state='OPEN'")]
        elif args.command == "confirm":
            if not all((args.message_id, args.question_id, args.input)):
                parser.error("confirm requires --message-id, --question-id and --input")
            with open(args.input, encoding="utf-8") as stream:
                confirmed = json.load(stream)
            output = asdict(service.confirm_input(args.message_id, args.question_id, Question.from_dict(confirmed["question"]),
                                                   verified_material=confirmed.get("verified_material")))
        else:
            from .workflow import Workflow
            output = Workflow(store).dashboard()["health"]
        print(encode(output))
    finally:
        store.close()


if __name__ == "__main__":
    main()
