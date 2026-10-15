"""Real MCP client (stdio) against the server: discover tools, guide, inspect, frame, patch, undo (A18 automated part)."""

import json
import sys
from pathlib import Path

import pytest

from explica.project import Project

from .conftest import needs_ffmpeg, needs_melt


async def _session(project_root: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "explica.mcp_server", "--project", str(project_root)])
    return stdio_client(params), ClientSession


def _payload(result) -> dict:
    text = next(c.text for c in result.content if getattr(c, "type", "") == "text")
    return json.loads(text)


@needs_ffmpeg
@needs_melt
@pytest.mark.anyio
async def test_mcp_roundtrip(timeline_project: Project):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = timeline_project.root
    params = StdioServerParameters(command=sys.executable, args=["-m", "explica.mcp_server", "--project", str(root)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            for name in ("project_guide", "project_inspect", "timeline_query", "media_frame", "scene_inspect", "scene_patch", "timeline_patch", "render_preview", "job_status", "job_cancel", "project_checkpoint", "project_restore", "project_validate", "project_export", "audio_segment", "transcript_query", "media_inspect", "kdenlive_sync"):
                assert name in tools, name
            guide = await session.call_tool("project_guide", {})
            assert "expected_revision" in guide.content[0].text
            insp = _payload(await session.call_tool("project_inspect", {}))
            rev = insp["revision"]
            assert insp["tracks"][1]["id"] == "V2"
            q = _payload(await session.call_tool("timeline_query", {"start_frame": 0, "end_frame": 100}))
            assert q["tracks"][1]["clips"][0]["source"]["ref"] == "s1"
            frame = await session.call_tool("media_frame", {"ref": "timeline", "frame": 30})
            kinds = [c.type for c in frame.content]
            assert "image" in kinds and "text" in kinds
            insp_scene = _payload(await session.call_tool("scene_inspect", {"scene_id": "s1", "frame": 30}))
            assert "title" in insp_scene["bounds"]
            dry = _payload(await session.call_tool("scene_patch", {"scene_id": "s1", "ops_json": json.dumps([{"op": "set", "path": "objects.title.text", "value": "Desde MCP"}]), "expected_revision": rev, "dry_run": True}))
            assert dry["dry_run"] and dry["diff"]
            applied = _payload(await session.call_tool("scene_patch", {"scene_id": "s1", "ops_json": json.dumps([{"op": "set", "path": "objects.title.text", "value": "Desde MCP"}]), "expected_revision": rev}))
            assert applied["ok"] and applied["revision"] == rev + 1
            conflict = _payload(await session.call_tool("scene_patch", {"scene_id": "s1", "ops_json": "[]", "expected_revision": rev}))
            assert conflict["ok"] is False and "revision conflict" in conflict["error"]
            cut = _payload(await session.call_tool("timeline_patch", {"ops_json": json.dumps([{"op": "trim_clip", "id": "c1", "out": 40}]), "expected_revision": rev + 1}))
            assert cut["ok"]
            job = _payload(await session.call_tool("render_preview", {"start_frame": 0, "end_frame": 30, "wait_seconds": 120}))
            assert job["status"] == "done", job
            assert Path(job["meta"]["out_path"]).exists()
            st = _payload(await session.call_tool("job_status", {"job_id": job["id"]}))
            assert st["status"] == "done"
            undo = _payload(await session.call_tool("project_restore", {}))
            assert undo["ok"] and undo["op"] == "project.undo"
            p = Project.load(root)
            _, c1 = p.find_clip("c1")
            assert c1["out"] == 60  # cut undone
            assert p.load_scene("s1")["objects"][0]["text"] == "Desde MCP"  # earlier patch still there
            val = _payload(await session.call_tool("project_validate", {}))
            assert val["ok"]
    log = (root / "mcp-server.log").read_text().splitlines()
    called = [json.loads(ln)["tool"] for ln in log]
    assert called[:2] == ["project_guide", "project_inspect"]
    assert "project_restore" in called
