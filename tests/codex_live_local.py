"""Opt-in real-model smoke test using an existing Codex home and sandbox."""
import argparse, json, os, subprocess, sys, tempfile, tomllib, uuid
from pathlib import Path
sys.stdout.reconfigure(errors="backslashreplace")
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', required=True)
    parser.add_argument('--codex-home', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8831/v1')
    parser.add_argument('--model', default='Qwen3.8-Flash-Next')
    args = parser.parse_args()
    home = Path(args.codex_home)
    original = tomllib.loads((home / 'config.toml').read_text(encoding='utf-8-sig'))
    name = 'bridge-live-' + uuid.uuid4().hex[:10]
    profile = home / (name + '.config.toml')
    with tempfile.TemporaryDirectory(prefix='bridge-live-', dir=ROOT) as directory:
        work = Path(directory)
        skill = work / '.agents/skills/bridge-live-check/SKILL.md'
        skill.parent.mkdir(parents=True)
        marker = 'SKILL_READ_' + uuid.uuid4().hex[:12]
        skill.write_text('---\nname: bridge-live-check\ndescription: Read this skill when validating the tool bridge.\n---\nThe validation marker is ' + marker + '. Include it in your final answer after reading this file.\n', encoding='utf-8')
        config = ['model=' + json.dumps(args.model), 'model_provider="bridge_live"',
                  'model_reasoning_effort="low"', 'web_search="disabled"',
                  'model_catalog_json=' + json.dumps(str(Path(args.catalog).resolve())),
                  '[features]', 'apps=false', 'plugins=false']
        for server in original.get('mcp_servers', {}):
            config += ['[mcp_servers.' + json.dumps(server) + ']', 'enabled=false']
        config += ['[mcp_servers.bridge_fixture]', 'command=' + json.dumps(sys.executable),
                   'args=' + json.dumps([str(ROOT / 'tests/fixtures/mcp_echo.py')]),
                   '[model_providers.bridge_live]', 'name="Local bridge live validation"',
                   'base_url=' + json.dumps(args.url), 'wire_api="responses"',
                   'requires_openai_auth=false', 'supports_websockets=false',
                   'request_max_retries=0', 'stream_max_retries=0']
        profile.write_text('\n'.join(config) + '\n', encoding='utf-8')
        if os.name == 'nt':
            acl = subprocess.run(['icacls', str(work), '/grant', 'CodexSandboxUsers:(OI)(CI)M', '/T', '/C'], capture_output=True)
            if acl.returncode:
                raise RuntimeError('Could not grant fixture-only sandbox access')
        prompt = ('Validate only this disposable workspace. First read the bridge-live-check SKILL.md at ' + str(skill) +
                  '. Then use the provided apply_patch tool (not a shell writer) to create proof.txt containing LIVE_PATCH_OK. '
                  'Call the namespaced bridge_fixture echo MCP tool with value LIVE_NAMESPACE_OK. '
                  'Then use exec_command to read proof.txt and remove only proof.txt for cleanup. '
                  'Do not stop before all four tool actions complete. Finish with the skill marker you read and the actual MCP result. Do not invent results.')
        env = os.environ.copy()
        env['CODEX_HOME'] = str(home)
        try:
            result = subprocess.run([args.codex, 'exec', '--profile', name, '--ephemeral', '--skip-git-repo-check',
                                     '--color', 'never', '-s', 'workspace-write', '-C', str(work), prompt],
                                    input='', capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=240)
            print(result.stderr[-16000:])
            print(result.stdout[-6000:])
            checks = {
                'exitOk': result.returncode == 0,
                'skillRead': marker in result.stdout,
                'patchExecuted': 'patch: completed' in result.stderr,
                'namespacedMcpExecuted': 'bridge_fixture/echo (completed)' in result.stderr,
                'mcpResultObserved': 'MCP_EXECUTED:LIVE_NAMESPACE_OK' in result.stdout,
                'patchReadBack': 'LIVE_PATCH_OK' in result.stderr,
            }
            print('LIVE_CHECKS', json.dumps(checks))
            assert all(checks.values()), 'Real-model tool check did not fully pass'
        finally:
            profile.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
