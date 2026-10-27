from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_news_api_distinguishes_automatic_from_explicit_requests():
    url = (ROOT / "src/frontend/static/js/modules/api.js").as_uri()
    script = f"""
import {{API}} from {json.dumps(url)};
const requests = [];
globalThis.fetch = async url => {{
 requests.push(url);
 return {{ok:true, json:async()=>({{listo:false, deferred:true}})}};
}};
await API.fetchNews({{automatic:true}});
await API.fetchNews();
if(requests[0] !== '/api/noticias?automatic=1') throw Error('automatic request bypassed quiet policy');
if(requests[1] !== '/api/noticias') throw Error('explicit request incorrectly suppressed');
"""
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=ROOT,
    )
