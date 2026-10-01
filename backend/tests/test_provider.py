from app.config import Config
from app.agent import build_context, request_tokens, compact_schema, SYSTEM, ResponseProposal, snapshot_tools, fit_request_context
from app.db import SessionLocal, Space, Goal, uid
from app.services import default_settings, seed_demo


def test_provider_selection_never_silently_falls_back():
    cfg = Config(_env_file=None, ai_provider="aliyun", aliyun_api_key="ali-test", nebius_api_key="nebius-test")
    assert cfg.model_api_key == "ali-test" and cfg.model_id == "qwen-plus"
    assert cfg.model_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cfg.aliyun_api_key = ""
    assert cfg.model_api_key == ""
    cfg.ai_provider = "nebius"
    assert cfg.model_api_key == "nebius-test" and cfg.model_id.startswith("nvidia/")


def test_compact_schema_keeps_real_title_properties_and_required_fields():
    value = compact_schema(ResponseProposal.model_json_schema())
    assert value["$defs"]["GoalInput"]["properties"]["title"]["type"] == "string"
    assert value["$defs"]["PlanTask"]["properties"]["title"]["type"] == "string"
    assert "title" in value["$defs"]["GoalInput"]["required"]
    assert "reply" in value["required"]


def test_full_request_budget_preserves_latest_human_and_authoritative_state():
    import copy
    import json
    context = {"messages": [{"role": "user", "text": "旧话题" * 400},
        {"role": "assistant", "text": "历史回答" * 300},
        {"role": "user", "text": "下一步是什么？"}],
        "memories": [{"id": "memory", "content": "当前范围由用户确认"}],
        "memory_dependencies": ["memory"],
        "goal": {"tasks": [{"id": "task", "status": "todo", "criteria": {"kind": "user"}}],
                 "evidence": [{"id": "evidence", "facts": {"passed": True}}]}}
    original = copy.deepcopy(context)
    tools = [{"type": "function", "function": {"name": "propose_followup",
        "parameters": compact_schema(ResponseProposal.model_json_schema())}}]
    messages = [{"role": "system", "content": SYSTEM + "\nTask: " + "遵守最新范围和已验证事实。" * 50},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    assert request_tokens(messages, tools) > 8000
    fitted = fit_request_context(context, messages, tools)
    assert request_tokens(messages, tools) <= 7872
    assert fitted["messages"][-1] == original["messages"][-1]
    assert fitted["goal"] == original["goal"]
    assert fitted["memory_dependencies"] == original["memory_dependencies"]
    assert fitted["context_truncated"] is True
    assert context == original


def test_realistic_five_task_workspace_fits_bounded_request():
    import json
    from sqlalchemy import delete
    from app.db import Base, engine, Task, Record
    Base.metadata.create_all(engine)
    identity = uid()
    try:
        with SessionLocal.begin() as db:
            space = Space(id=identity, role="demo", settings=default_settings())
            db.add(space)
            db.flush()
            seed_demo(db, space)
            context = build_context(db, space, db.query(Goal).filter_by(space_id=identity).one())
        tools = snapshot_tools() + [{"type": "function", "function": {"name": name,
            "parameters": compact_schema(ResponseProposal.model_json_schema())}} for name in ("propose_plan_patch", "propose_followup")]
        messages = [{"role":"system", "content":SYSTEM}, {"role":"user", "content":json.dumps(context, ensure_ascii=False)}]
        assert request_tokens(messages, tools) < 8000
    finally:
        with SessionLocal.begin() as db:
            goals = [r.id for r in db.query(Goal).filter_by(space_id=identity)]
            db.execute(delete(Record).where(Record.space_id==identity))
            db.execute(delete(Task).where(Task.goal_id.in_(goals)))
            db.execute(delete(Goal).where(Goal.space_id==identity))
            db.execute(delete(Space).where(Space.id==identity))
