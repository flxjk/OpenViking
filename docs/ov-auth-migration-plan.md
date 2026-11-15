# OpenViking Auth Migration Plan — dev → api_key / trusted

Status: **plan only, nothing applied.** Runtime changes (editing `ov.conf`, changing client
configs, restarting services) are owned by other agents.

Repo: `/Users/gj/git/OpenViking-main`, branch `dev`, commit `dfcd290de`.
Server config file in use: **`/Users/gj/git/OpenViking-main/ov.conf`** (NOT `~/.openviking/ov.conf`).

Every claim below cites `file:line`. Facts re-verified by reading the source on this branch.

---

## 1. Current state (verified)

Live server on `127.0.0.1:1933`:

```
$ curl -s http://127.0.0.1:1933/health
{"status":"ok","healthy":true,"version":"0.1.dev109","auth_mode":"dev"}
```

`auth_mode: "dev"` is auto-selected: `ov.conf` has no `server.auth_mode` and no
`server.root_api_key`, so `get_effective_auth_mode()` returns `dev`
(`openviking/server/config.py:434-446`):

```python
auth_mode_text = str(self.auth_mode).strip() if self.auth_mode is not None else ""
if auth_mode_text:
    return auth_mode_text
if self.root_api_key is not None and str(self.root_api_key).strip():
    return AuthMode.API_KEY.value
return AuthMode.DEV.value
```

Dev mode = no credential, identity always `ROOT` / account `default` / user `default`
(`openviking/server/auth/plugins/dev.py:32-40`):

```python
return ResolvedIdentity(
    role=Role.ROOT,
    account_id=x_openviking_account or "default",
    user_id=x_openviking_user or "default",
)
```

Admin API is unavailable in dev mode — verified against the live server:

```
$ curl -s http://127.0.0.1:1933/api/v1/admin/accounts -H "X-API-Key: x"
{"status":"error",...,"code":"PERMISSION_DENIED",
 "message":"Admin API requires api_key mode with root_api_key configured. Development mode does not support account or user management. ..."}
```

That message is `_DEV_MODE_ADMIN_API_MESSAGE`
(`openviking/server/auth/__init__.py:367-370`), raised by the
`require_auth_role` decorator when `request.app.state.api_key_manager` is None
(`__init__.py:406-407`) or by `get_api_key_manager_or_raise` (`__init__.py:442-447`).

Note: dev mode *does* construct an account-store facade with
`issue_user_keys=False` in the **working tree** (`dev.py:65-77`, an uncommitted change
— see §3.2), so the manager is not always None. In the currently **running** process the
committed version applied, which set `api_key_manager = None`. The observed 403 is the
live behaviour either way: `/api/v1/admin/accounts` returns exactly this error in dev
mode. **Dev mode cannot bootstrap accounts via this route as the server runs today**,
which is the operative fact.

Valid modes (`openviking/server/identity.py:131-139`): `dev`, `api_key`, `trusted`
(plus OIDC/LDAP plugins).

Client inventory — all keyless today, all depending on dev mode:

| Client | File | Current credential |
|---|---|---|
| ov CLI | `~/.openviking/ovcli.conf` | none (`url`, `account`, `user`) |
| Gemini CLI | `/Users/gj/.gemini/config/mcp_config.json` | none (`serverUrl` only) |
| DSH memory plugin | `examples/dsh-memory-plugin/` | resolves empty key |
| VikingBot | `ov.conf` | no `bot` section at all |

---

## 2. THE ROOT-KEY TRAP (read before doing anything)

In `api_key` mode a **ROOT key is refused on tenant data APIs.** Allowlist
(`openviking/server/auth/plugins/api_key.py:22-43`):

```python
_API_KEY_ROOT_ALLOWED_PATHS = {
    "/api/v1/system/status",
    "/api/v1/system/wait",
    "/api/v1/debug/health",
}
_API_KEY_ROOT_ALLOWED_PREFIXES = (
    "/api/v1/admin",
    "/api/v1/observer",
    "/api/v1/console",
    "/api/v1/tasks",
)
```

Enforced in `get_request_context_checks` (`api_key.py:207-217`):

```python
if (
    identity.role == Role.ROOT
    and not identity.from_oauth
    and not _api_key_root_can_access_path(path)
):
    raise PermissionDeniedError(
        "ROOT API keys cannot access tenant-scoped data APIs in api_key mode. "
        "Use a user/admin API key for data access, or trusted mode for upstream "
        "identity assertion."
    )
```

Consequences in `api_key` mode:

- `/mcp`, `/api/v1/fs/*`, `/api/v1/search/*` → **403 with the root key**. The
  `/mcp` endpoint uses the same resolver (`openviking/server/mcp_endpoint.py:190-220`
  `_IdentityASGIMiddleware` calls `resolve_identity`), so MCP is not exempt.
- **A USER or ADMIN key is mandatory for all data access.**

This is documented too: `docs/en/agent-integrations/15-agent-plugins.md:60` — *"Use a
User/Admin key for `/mcp`. The legacy `server.root_api_key` fallback does not make a
root key valid for MCP access."*

Additional trap in the `ov` CLI (`crates/ov_cli/src/config.rs:369-375`):

```rust
let api_key = if sudo {
    self.root_api_key.clone()
} else {
    api_key_override
        .or_else(|| self.api_key.clone())
        .or_else(|| self.root_api_key.clone())
};
```

Without `api_key` set, ordinary commands **fall back to `root_api_key`** → 403. So in
`api_key` mode `ovcli.conf` must carry a USER `api_key`, with `root_api_key` present
only for `--sudo`.

Also fatal at startup: an **explicitly empty** `root_api_key` string exits the process
(`openviking/server/config.py:648-656`). Omit the key entirely, never set it to `""`.

---

## 3. Bootstrap sequence for `api_key` mode on the existing store

### 3.1 Where the data lives — the answer is good news

Disk mapping. `_generate_plugin_config` mounts `localfs` at `/local` with
`local_dir = <workspace>/viking` (`openviking/utils/agfs_utils.py:401-417, 440-445`):

```python
vikingfs_path = data_path / "viking"
...
config[plugin_name] = {
    "enabled": True,
    "path": "/local",
    "config": mount_config,      # {"local_dir": "<workspace>/viking"}
}
```

Verified by executing the generator against a scratch workspace — it emits
`"localfs": {"path": "/local", "config": {"local_dir": "<ws>/viking"}}`.

URI → path is a **pure prefix replacement keyed on the request's account_id**
(`openviking/storage/viking_fs/_access.py:903-918`):

```python
def _uri_to_path(self, uri: str, ctx: Optional[RequestContext] = None) -> str:
    """Map virtual URI to account-isolated AGFS path.

    Pure prefix replacement: viking://{remainder} -> /local/{account_id}/{remainder}.
    No implicit space injection — URIs must include space segments explicitly.
    """
    real_ctx = self._ctx_or_default(ctx)
    account_id = real_ctx.account_id
    parts = self._safe_uri_parts(uri)
    ...
    return f"/local/{account_id}/{'/'.join(safe_parts)}"
```

User space is the bare `user_id`, with no account segment in the URI
(`openviking/core/namespace.py:163-165`):

```python
def canonical_user_root(ctx: RequestContext) -> str:
    return f"viking://user/{user_space_fragment(ctx)}"

def user_space_fragment(ctx: RequestContext) -> str:
    return ctx.user.user_id
```

**Therefore:** `viking://user/default` with account `default` →
`/local/default/user/default` → `data/viking/default/user/default`.

That is the **existing** directory (`data/viking/default/user/default/memories/...`,
`peers/...`). Because api_key mode resolves a USER key minted for
`account_id="default"`, `user_id="default"` to exactly that identity, **existing
memories remain visible and in place.** api_key mode does *not* move data to another
namespace — the namespace was always `{account_id}/{user_id}`, and dev mode also
hardcoded both to `default`.

Corollary: minting the USER key for any account/user *other* than `default`/`default`
produces an empty space. The bootstrap below deliberately targets `default`/`default`.

### 3.2 The account registry, and the one real risk

Account/user identities are **not** stored in the same place as memories. They live in
AGFS registry files (`openviking/server/account_stores/file.py:56-58`):

```python
ACCOUNTS_PATH = "/local/_system/accounts.json"
USERS_PATH_TEMPLATE = "/local/{account_id}/_system/users.json"
GROUPS_PATH_TEMPLATE = "/local/{account_id}/_system/groups.json"
```

On disk that is `data/viking/_system/accounts.json` and
`data/viking/default/_system/users.json`.

`FileStore.load()` bootstraps the `default` account lazily on first run
(`file.py:169-180`):

```python
accounts_data = await self._read_json(ACCOUNTS_PATH)
if accounts_data is None:
    # First run: create default account
    now = datetime.now(timezone.utc).isoformat()
    accounts_data = {"accounts": {"default": {"created_at": now}}}
    await self._write_json(ACCOUNTS_PATH, accounts_data)
```

Dev mode runs this too, with `issue_user_keys=False` (`dev.py:65-77`), so a
`default` account entry is expected in the registry — **with no users and no keys**.

⚠️ **`create_account("default", ...)` will therefore FAIL with `AlreadyExistsError`.**
`_create_account_identity` rejects an existing account twice
(`file.py:420-421` and `file.py:1299-1301`):

```python
if account_id in self._accounts:
    raise AlreadyExistsError(account_id, "account")
...
for account_id in reject_existing_account_ids or set():
    if account_id in accounts:
        raise AlreadyExistsError(account_id, "account")
```

**Do not try to create the `default` account.** Instead, register the `default` **user**
inside the already-existing `default` account — that is the correct and only needed call.

**Anomaly — RESOLVED.** The code says `load()` must write `accounts.json`, but on this
machine that file does **not** exist:

```
$ ls -la data/viking/_system/
total 0
drwxr-xr-x@ 2 gj  staff  64 Nov 15 04:42 .
drwxr-xr-x@ 5 gj  staff 160 Nov 10 15:50 ..
$ find data -name accounts.json -o -name users.json -o -name groups.json
(none)
```

The explanation is an **uncommitted working-tree change by another agent**. The
*committed* `dev.py` does not build a store at all:

```diff
$ git diff openviking/server/auth/plugins/dev.py
-    async def initialize(self, app, service, config) -> None:
-        """Dev mode does not need an APIKeyManager."""
-        app.state.api_key_manager = None
+    async def initialize(self, app, service, config) -> None:
+        """Dev mode builds the account store so the Admin API is usable locally.
...
+        await manager.load()
```

So in the **running** (older) process dev mode set `api_key_manager = None` and never
called `FileStore.load()` — hence no `accounts.json` on disk. This is fully consistent
with the empty `_system/` directory and with the observed live 403 (§1).

**Consequences for this plan — important:**

1. The running server is **stale** relative to the working tree. Restarting it picks up
   the modified `dev.py`, at which point dev mode itself will call `load()` and seed
   `data/viking/_system/accounts.json` with the `default` account.
2. After such a restart, dev mode also becomes admin-capable (the accompanying
   `tests/server/test_dev_mode_admin_capability.py` pins this intent), and
   `has_implicit_manage` is widened to ROOT (`openviking/storage/acl.py:298-299`).
3. **This does not change the bootstrap for `api_key` mode.** Either way, once the
   server runs in `api_key` mode `load()` seeds `{"accounts":{"default":...}}` and the
   `default` **user** must be registered inside that existing account — Step 5 of §7 is
   unchanged. What changes is only *when* the seed happens.
4. Coordinate with the agent making that change before restarting: if dev mode becomes
   admin-capable, an alternative (and simpler) bootstrap is to bootstrap **while still
   in dev mode**, then flip `auth_mode`. Both work; pick one deliberately rather than
   arriving at it by accident.

**Executor must still verify the actual state before relying on `register_user`**, using
§7 Step 2. The three cases:

- `accounts.json` absent → `load()` seeds `default` on the next start; Step 5 is correct.
- `accounts.json` present with `default` and no users → Step 5 is correct.
- `accounts.json` present with `default` already holding a user → skip Step 5, use the
  rotate endpoint instead.

### 3.3 Exact API calls

**Create user `default` (the key step) — `POST /api/v1/admin/accounts/{account_id}/users`**

Route `openviking/server/routers/admin.py:986-1022`, request model
`admin.py:78-82`:

```python
class RegisterUserRequest(BaseModel):
    user_id: str
    role: str = "user"
    seed: str | None = None
    user_config: UserConfig | None = None
```

**Yes — user creation returns the key directly. No separate rotate call is needed.**
`admin.py:1019-1021`:

```python
result = {
    "account_id": account_id,
    "user_id": body.user_id,
}
if should_expose_user_key(request):
    result["user_key"] = user_key
return Response(status="ok", result=result)
```

`should_expose_user_key` returns True unless the mode is `trusted`
(`openviking/server/auth/__init__.py:450-457`), so in `api_key` mode the response
contains `result.user_key`.

Key format is `base64url(account_id).base64url(user_id).base64url(secret)` — three
dot-separated segments (`openviking/server/api_keys/key_mgr.py:26-33, 37-38, 58-62`).
A `seed` makes the key deterministic (`key_mgr.py:52-55`, SHA-256 of `user_id\0seed`);
omit it to get a random one.

Role is validated by `validate_account_user_creation_role`, allowed set
`(USER, ADMIN, GLOBAL)` (`openviking/server/identity.py:123-124`); `GLOBAL` is
ROOT-only (`admin.py:994-999`). Use `"user"` — or `"admin"` if you want this identity
to manage other users later.

**Rotate/issue a further key** — `POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key`
(`admin.py:1228-1256`). Body `{"seed": null}` optional; returns
`{"user_key": <new>}` and **immediately invalidates the old key**. Use only to recover
or deliberately rotate.

**Create account (only for a NEW account, NOT `default`)** —
`POST /api/v1/admin/accounts` (`admin.py:459-527`), body
`CreateAccountRequest{account_id, admin_user_id, seed?, user_config?, settings?}`
(`admin.py:68-75`). Returns `{"account_id","admin_user_id","user_key"}`. It also runs
`initialize_account_workspace` and writes initial user config. **This is the call that
would fail for `default`.**

**There is no data migration.** `POST /api/v1/admin/migrate` is a deliberate stub that
always raises (`admin.py:560-566`):

```python
@router.post("/migrate", status_code=400)
@require_auth_root
async def migrate_data(request, ctx):
    """Reserve the ROOT migration endpoint without running a migration."""
    raise InvalidArgumentError("Data migration is not currently supported.")
```

Good news: none is needed, because the namespace is unchanged (§3.1).

### 3.4 Minimal ordered curl sequence

Assumes `ROOT_KEY` is the new `server.root_api_key`, and that the server has been
restarted into `api_key` mode (Step 4 in §7). Run from a shell where
`ROOT_KEY` is exported.

```bash
ROOT_KEY='<the value of server.root_api_key you set in ov.conf>'

# 1. Confirm the server really is in api_key mode.
curl -s http://127.0.0.1:1933/health
# expect: "auth_mode":"api_key"

# 2. Confirm the ROOT key works on the control plane.
curl -s http://127.0.0.1:1933/api/v1/admin/accounts \
  -H "X-API-Key: $ROOT_KEY"
# expect: status ok; result.accounts should include "default"

# 3. Register the USER inside the EXISTING default account, and capture the key.
curl -s -X POST http://127.0.0.1:1933/api/v1/admin/accounts/default/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ROOT_KEY" \
  -d '{"user_id":"default","role":"user"}'
# expect: {"status":"ok","result":{"account_id":"default","user_id":"default",
#                                  "user_key":"ZGVmYXVsdA.ZGVmYXVsd.<secret>"}}

# 4. Persist it for the client configs.
export USER_KEY='<paste result.user_key from step 3>'

# 5. Prove the USER key can read EXISTING data (this is the acceptance test).
curl -s "http://127.0.0.1:1933/api/v1/fs/ls?uri=viking://user/default/memories" \
  -H "X-API-Key: $USER_KEY"
# expect: status ok, listing memories (this is pre-existing dev-mode data)

# 6. Prove the trap: the ROOT key is refused on the same data path.
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1:1933/api/v1/fs/ls?uri=viking://user/default/memories" \
  -H "X-API-Key: $ROOT_KEY"
# expect: 403
```

Optional — an ADMIN variant if you want a second identity for administration:

```bash
curl -s -X POST http://127.0.0.1:1933/api/v1/admin/accounts/default/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ROOT_KEY" \
  -d '{"user_id":"admin","role":"admin"}'
```

Note: an `admin` key can manage users in `default`, but its **data** namespace is
`viking://user/admin/...`, which is empty. Use the `default` USER key for data.

---

## 4. Target `ov.conf` blocks (NOT applied)

Base file is `/Users/gj/git/OpenViking-main/ov.conf`. Keep the existing `storage`,
`embedding`, `vlm` and `gateway` sections; change only `server` and add `bot`.

### 4.1 Target A — `api_key` mode (recommended primary)

```json
"server": {
  "host": "127.0.0.1",
  "port": 1933,
  "auth_mode": "api_key",
  "root_api_key": "<ROOT_KEY — long random, never empty>"
}
```

`auth_mode` is stated explicitly so the mode cannot drift if `root_api_key` is later
removed. Generate with `openssl rand -hex 32` (or similar).

### 4.2 Target B — `trusted` mode

```json
"server": {
  "host": "127.0.0.1",
  "port": 1933,
  "auth_mode": "trusted",
  "root_api_key": "<ROOT_KEY — MANDATORY even on localhost>"
}
```

Trusted mode **requires** a non-empty `root_api_key` even on localhost — the plugin
exits otherwise (`openviking/server/auth/plugins/trusted.py:225-233`):

```python
def validate_config(self, config) -> None:
    if config.root_api_key and config.root_api_key.strip():
        return
    logger.error(
        "Cannot start OpenViking: trusted mode requires a non-empty server.root_api_key, "
        "including on localhost. Set server.root_api_key in the server's ov.conf and "
        "restart. Configure clients and Web Studio to use the same key."
    )
    sys.exit(1)
```

In trusted mode every request must still present the root key as `X-API-Key`, and
identity is asserted via headers (`trusted.py:139-195`). Role defaults to USER unless
the identity is registered or asserted (`trusted.py:196-201`):

```python
local_role = None
if asserted_role is not None:
    trusted_role = asserted_role
else:
    if self._api_key_manager and effective_account_id and effective_user_id:
        local_role = await self._api_key_manager.get_registered_user_role(
            effective_account_id, effective_user_id
        )
    trusted_role = local_role or Role.USER
```

`/api/v1/admin/*` with the root key resolves to ROOT (`trusted.py:172-186`):

```python
is_admin_path = _is_trusted_admin_path(request.url.path)
...
if is_admin_path or is_control_probe:
    return ResolvedIdentity(
        role=Role.ROOT,
        account_id=effective_account_id or "trusted",
        user_id=effective_user_id or "trusted",
    )
```

`_is_trusted_admin_path` is `path == "/api/v1/admin" or startswith("/api/v1/admin/")`
(`trusted.py:71-72`).

**Trusted mode is the only mode in which the root key can reach data APIs**, because the
ROOT-key allowlist in §2 applies to `api_key` mode only. That is the mode's whole point:
an upstream is trusted, and it asserts the user it is acting for.

### 4.3 Which clients must change, per mode

| Mode | Client | Must change? | Exact credential / header |
|---|---|---|---|
| `api_key` | ov CLI | **yes** | `api_key` = USER key; `root_api_key` = ROOT key (for `--sudo`) |
| `api_key` | Gemini MCP | **yes** | `X-API-Key: <USER key>` on `serverUrl` |
| `api_key` | DSH plugin | **yes** | USER key, `authMode: "api_key"` |
| `api_key` | VikingBot | **yes** | `bot.ov_server.api_key` = USER key, `api_key_type: "user"` |
| `trusted` | ov CLI | **yes** | `api_key` = ROOT key (trusted needs it on every request) |
| `trusted` | Gemini MCP | **yes** | `X-API-Key: <ROOT key>` + `X-OpenViking-Account` + `X-OpenViking-User` |
| `trusted` | DSH plugin | **yes** | `authMode: "trusted"`; root key; sends identity headers |
| `trusted` | VikingBot | **yes** | `bot.ov_server.api_key` = ROOT key, `api_key_type: "root"` |

---

## 5. Client-config deltas (plan only — NOT applied)

### 5.1 `~/.openviking/ovcli.conf`

Read by the `ov` CLI (`crates/ov_cli/src/config.rs:39-88`; `api_key`, `root_api_key`,
`account`, `user` are all modelled fields) and by the DSH/agent-plugins credential chain
(`examples/dsh-memory-plugin/shared/credentials.mjs:265-279`).

**api_key mode** (§4.1) — note `api_key` for data, `root_api_key` for `--sudo`:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "<USER_KEY>",
  "root_api_key": "<ROOT_KEY>",
  "account": "default",
  "user": "default"
}
```

`--sudo` reads `root_api_key` (`config.rs:369-370`) and requires it to be present
(`crates/ov_cli/src/app/mod.rs:294-300`: *"--sudo requires root_api_key in
~/.openviking/ovcli.conf."*). Without `api_key`, ordinary commands would fall back to
`root_api_key` and hit the §2 trap.

**trusted mode** (§4.2) — the root key must be the ordinary key too:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "<ROOT_KEY>",
  "root_api_key": "<ROOT_KEY>",
  "account": "default",
  "user": "default"
}
```

⚠️ The `ov` CLI sends `X-API-Key` (`docs/en/agent-integrations/16-capability-reference.md:550`:
*"`X-API-Key`; Basic or Bearer depending on `auth_mode`"*). In trusted mode that key
must match `server.root_api_key`, and `account`/`user` become the **asserted** identity.

### 5.2 `/Users/gj/.gemini/config/mcp_config.json`

Currently (verified):

```json
"openviking": {
  "disabled": false,
  "serverUrl": "http://127.0.0.1:1933/mcp"
}
```

The `/mcp` endpoint authenticates through the **same** resolver as REST
(`openviking/server/mcp_endpoint.py:190-220`), accepting `X-API-Key` or
`Authorization: Bearer` (`openviking/server/auth/__init__.py:41-50`).

**api_key mode — add the header with the USER key:**

```json
"openviking": {
  "disabled": false,
  "serverUrl": "http://127.0.0.1:1933/mcp",
  "headers": {
    "X-API-Key": "<USER_KEY>"
  }
}
```

**trusted mode — root key plus asserted identity:**

```json
"openviking": {
  "disabled": false,
  "serverUrl": "http://127.0.0.1:1933/mcp",
  "headers": {
    "X-API-Key": "<ROOT_KEY>",
    "X-OpenViking-Account": "default",
    "X-OpenViking-User": "default"
  }
}
```

Caveat to check at execution time: the key `headers` is the field Gemini CLI uses for
HTTP MCP servers. Confirm against your Gemini CLI version before relying on it; the
repo's own docs warn that a static `headers` map is not portable across clients
(`docs/en/agent-integrations/15-agent-plugins.md:35`). If Gemini rejects `headers`, the
supported path is the bundled **stdio proxy**, which reads the same credential files as
the CLI and injects the header per request — that is what the DSH plugin does.

### 5.3 VikingBot — `bot.ov_server` block

Read from `ov.conf`'s `bot` section (`bot/vikingbot/config/loader.py:79-104`), whose
`ov_server` is `OpenVikingConfig` (`bot/vikingbot/config/schema.py:689-710`), with fields
`mode`, `api_key_type`, `server_url`, `api_key`, `account_id`, `admin_user_id`.

**api_key mode — a USER key is mandatory; a root key is a hard exit**
(`loader.py:662-688`):

```python
def _validate_api_key_mode_key(ov_server, server_url):
    api_key = str(getattr(ov_server, "api_key", "") or "").strip()
    api_key_type = _normalize_api_key_type(getattr(ov_server, "api_key_type", "user")) or "user"
    if _ov_server_is_server_managed(ov_server):
        return
    if not api_key or api_key_type != "user":
        _raise_api_key_mode_requires_user_key(ov_server)
    ...
    if role in {"user", "admin"} and account_id and user_id:
        return
    if role == "root":
        _raise_api_key_mode_root_key()
    _raise_api_key_mode_requires_user_key(ov_server)
```

```json
"bot": {
  "ov_server": {
    "server_url": "http://127.0.0.1:1933",
    "api_key": "<USER_KEY>",
    "api_key_type": "user",
    "account_id": "default",
    "admin_user_id": "default"
  }
}
```

**trusted mode — `api_key_type: "root"`** (`loader.py:306-317`):

```python
api_key_type = _normalize_api_key_type(bot_data.get("api_key_type")) or (
    "root" if server_auth_mode == "trusted" else "user"
)
...
if api_key_type == "root" and server_auth_mode == "trusted" and server_root_api_key:
    bot_data["api_key"] = server_root_api_key
    api_key_source = "server.root_api_key"
```

```json
"bot": {
  "ov_server": {
    "server_url": "http://127.0.0.1:1933",
    "api_key_type": "root",
    "api_key": "<ROOT_KEY>",
    "account_id": "default",
    "admin_user_id": "default"
  }
}
```

In trusted mode, if `bot.ov_server.api_key` is left empty the loader inherits
`server.root_api_key` automatically (`loader.py:314-317`) — but set it explicitly so the
intent is legible.

Note the bot resolves `ov.conf` as `OPENVIKING_CONFIG_FILE` or `~/.openviking/ov.conf`
(`loader.py:37-45`) — **the same repo file only if that env var points at it.** Confirm
the bot's config path at execution time; otherwise the `bot` block must go in whichever
file the bot actually reads.

### 5.4 DSH memory plugin

Located in-repo at `examples/dsh-memory-plugin/` and installed under
`~/.dsh/profiles/web/...`. It reads **`~/.openviking/ovcli.conf` and
`~/.openviking/ov.conf`** — not the repo file
(`shared/credentials.mjs:6-7, 82-91`).

Resolution order (`credentials.mjs:261-290`): host input → env
(`OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`) → `ovcli.conf` `api_key` → its
`plugin` keys → ov.conf harness section → `server.root_api_key` (only with
`rootKeyFallback`).

Header emission (`shared/ov-http.mjs:26-35`):

```js
const identity = identityHeaders === undefined ? Boolean(cfg.sendIdentityHeaders) : Boolean(identityHeaders);
const headers = { "Content-Type": "application/json" };
if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
if (identity && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
if (identity && cfg.user) headers["X-OpenViking-User"] = cfg.user;
```

So the plugin sends `Authorization: Bearer <key>` — **never `X-API-Key`** — and the
identity headers go out **only** when `sendIdentityHeaders` is true, which is set purely
from the resolved mode (`credentials.mjs:310`):

```js
sendIdentityHeaders: authMode === "trusted",
```

and the mode is inferred as (`credentials.mjs:287-290`):

```js
const authMode = normalizeAuthMode(host.authMode)
  || normalizeAuthMode(env.OPENVIKING_AUTH_MODE)
  || (fromFiles && (authModeIn(plugin) || authModeIn(section) || normalizeAuthMode(server.auth_mode)))
  || ((account || user) ? "trusted" : "api_key");
```

⚠️ That **last line is a real hazard**: `ovcli.conf` currently has `account` and `user`
set and no explicit mode, so the chain infers `trusted`. In `api_key` mode that would
send `X-OpenViking-Account`/`X-OpenViking-User` — which the api_key plugin silently
strips (`api_key.py:82-90`), so it is harmless but misleading. Setting the mode
explicitly removes the ambiguity.

**api_key mode** — ensure the inferred mode is right:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "<USER_KEY>",
  "account": "default",
  "user": "default",
  "plugin": {
    "dsh_memory": { "authMode": "api_key" }
  }
}
```

**trusted mode** — the inference already lands on `trusted`, but state it:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "<ROOT_KEY>",
  "account": "default",
  "user": "default",
  "plugin": {
    "dsh_memory": { "authMode": "trusted" }
  }
}
```

Alternatively set `OPENVIKING_AUTH_MODE` in the plugin's environment. `plugin.<harness>`
keys are read with the harness name normalized (`-`→`_`), both spellings accepted
(`credentials.mjs:148-175`).

---

## 6. Embedded gateway conflict — assessment

`ov.conf` currently sets `gateway.enabled: true` with a `management_key`. The gateway
**refuses trusted mode outright** (`openviking_gateway/client.py:66-70`):

```python
async def load():
    result = await self.request("GET", "/health", credential, timeout=5)
    if result.get("auth_mode") == "trusted":
        raise VikingError("trusted_mode_unsupported", 403)
```

and again in `health()` (`openviking_gateway/client.py:92-96`):

```python
if result.get("auth_mode") == "trusted":
    raise VikingError("trusted_mode_unsupported", 403)
```

It also refuses a root key for data outside dev mode (`client.py:84-86`):

```python
if result["role"] == "root" and result.get("auth_mode") != "dev":
    raise VikingError("root_key_not_allowed", 403)
```

**What actually breaks in trusted mode:** any gateway path that calls `identify()` or
`health(require_identity=True)` gets `trusted_mode_unsupported`, so the gateway cannot
establish a caller identity. Combined with the root-key refusal, the gateway has **no
working credential** in trusted mode.

**What does NOT break:** the server itself. The gateway is an embedded component
(`openviking/server/app.py:434-451`); the OpenViking REST API, `/mcp`, the CLI, and the
memory plugins do not route through `openviking_gateway.client`. Dev mode currently
works because the gateway's `identify()` special-cases it
(`client.py:70-72`):

```python
if result.get("auth_mode") == "dev":
    result = {**result, "role": "root", "account_id": "default", "user_id": "default"}
```

Note also that `_IdentityASGIMiddleware`'s own health path and the `/health` endpoint
report `auth_mode` directly from config (`openviking/server/routers/system.py:54-61`),
which is what the gateway inspects.

**Recommendation — pick one:**

1. **Preferred: run `api_key` mode as the primary and keep the gateway enabled.**
   `api_key` mode satisfies the gateway (it is not `trusted`, and a USER key resolves to
   a non-root role), so nothing is sacrificed for the memory/CLI/MCP workflows. The
   ROOT-key trap is handled by giving every data client a USER key (§4.3).
2. **If trusted mode is required: set `gateway.enabled: false`** for that deployment and
   document why. Trusted mode is for an upstream that asserts identity; the gateway is
   designed to own and mint credentials instead (`openviking/server/auth/__init__.py:450-457`:
   *"Trusted gateways own credentials; never expose server keys in that mode."*).
   The gateway cannot do that under trusted mode today.
3. Do **not** expect "both at once" on one server: `auth_mode` is a single value
   (`config.py:391`), so a process is either `api_key` or `trusted`, never both.

`gateway.enabled: true` is confirmed present in the current `ov.conf`, and the gateway
data dir exists (`data/gateway/`, `~/.openviking/gateway.key`).

---

## 7. Executor runbook (ordered, with expected output)

> Prerequisite: confirm the intended `ov.conf` path with the other agents. The repo file
> is `/Users/gj/git/OpenViking-main/ov.conf`; the `ov` CLI, the DSH plugin and VikingBot
> all default to `~/.openviking/ov.conf`. **These differ today.** The bot and plugin read
> `~/.openviking/ov.conf` unless `OPENVIKING_CONFIG_FILE` overrides it
> (`bot/vikingbot/config/loader.py:37-45`,
> `examples/dsh-memory-plugin/shared/credentials.mjs:6-7, 82-91`). Resolve this before
> starting.

**Step 0 — Back up. Non-negotiable.**

```bash
cd /Users/gj/git/OpenViking-main
cp ov.conf "ov.conf.bak-$(date +%Y%m%d-%H%M%S)"
cp ~/.openviking/ovcli.conf "$HOME/.openviking/ovcli.conf.bak-$(date +%Y%m%d-%H%M%S)"
cp /Users/gj/.gemini/config/mcp_config.json \
   "/Users/gj/.gemini/config/mcp_config.json.bak-$(date +%Y%m%d-%H%M%S)"
tar czf "data-viking-backup-$(date +%Y%m%d-%H%M%S).tgz" data/viking
```
Expected: files created; note the tar name for rollback.

**Step 1 — Generate the root key.**

```bash
openssl rand -hex 32
```
Expected: 64 hex chars. Store as `ROOT_KEY`.

**Step 2 — Record the pre-migration registry state (resolves §3.2's anomaly).**

```bash
ls -la /Users/gj/git/OpenViking-main/data/viking/_system/
find /Users/gj/git/OpenViking-main/data -name accounts.json -o -name users.json
```
Expected today: **empty / no files.** Record exactly what you see.
- If `accounts.json` appears after the Step 4 restart, `load()` seeded `default` and
  Step 5's `register_user` is correct.
- If `accounts.json` already exists with `default` holding users, skip Step 5 and use
  the rotate endpoint instead.

**Step 3 — Edit `ov.conf`** to Target A (§4.1): add `auth_mode: "api_key"` and
`root_api_key: "<ROOT_KEY>"`. Leave `gateway.enabled: true`. Re-read §2 first.
Expected: valid JSON (validate with `python3 -m json.tool ov.conf`).

**Step 4 — Restart the server** (owned by another agent).

⚠️ The running process is **stale relative to the working tree**: there are uncommitted
changes to `openviking/server/auth/plugins/dev.py` (dev mode now builds a real account
store and calls `load()`), plus `openviking/storage/acl.py` and a new test
`tests/server/test_dev_mode_admin_capability.py`. A restart therefore does **two** things
at once: switches the auth mode *and* picks up the dev-mode admin change. Confirm with
that agent that the tree is intended to be deployed, and re-read §3.2 point 4.

Then:

```bash
curl -s http://127.0.0.1:1933/health
```
Expected: `"auth_mode":"api_key"`.
If the process exits immediately, the cause is an empty/invalid `root_api_key`
(`config.py:648-656`) — check for `""`.

**Step 5 — Bootstrap the USER key** (§3.4 step 3):

```bash
export ROOT_KEY='<ROOT_KEY>'
curl -s -X POST http://127.0.0.1:1933/api/v1/admin/accounts/default/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ROOT_KEY" \
  -d '{"user_id":"default","role":"user"}'
```
Expected: `{"status":"ok","result":{"account_id":"default","user_id":"default","user_key":"..."}}`.
- `ALREADY_EXISTS` → the account already has this user; use the rotate endpoint instead.
- `PERMISSION_DENIED` with the dev-mode message → the server is still in dev mode;
  return to Step 3/4.

**Step 6 — Verify existing memories are readable with the USER key:**

```bash
export USER_KEY='<from Step 5>'
curl -s "http://127.0.0.1:1933/api/v1/fs/ls?uri=viking://user/default/memories" \
  -H "X-API-Key: $USER_KEY"
```
Expected: `status ok` with the pre-existing memory listing. **This is the acceptance
test for "existing memories remain visible".** If it is empty, the key's account/user
does not match `default`/`default` — re-check the key's first two segments decode to
`default`/`default` (`key_mgr.py:37-49`).

**Step 7 — Confirm the trap behaves as documented:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1:1933/api/v1/fs/ls?uri=viking://user/default/memories" \
  -H "X-API-Key: $ROOT_KEY"
```
Expected: `403`.

**Step 8 — Apply client configs** per §5.1–5.4 with `USER_KEY` (api_key mode).
Then verify each:

```bash
# ov CLI
ov admin list-users default            # or: ov fs ls viking://user/default/memories
# MCP (through whatever client you use)
curl -s -X POST http://127.0.0.1:1933/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "X-API-Key: $USER_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
Expected for MCP: a tool list, **not** 401/403. A 403 here means a root key was used.

**Step 9 — If trusted mode is also wanted:** it is a **separate deployment**, not a
second mode on this process. Apply §4.2, regenerate client configs per the trusted
column of §4.3, and set `gateway.enabled: false` (§6). Re-run Steps 4, 6, 7 adapted:
in trusted mode the root key **is** valid on data APIs when identity headers are
supplied, so Step 7's expected result becomes `200`.

---

## 8. Rollback

Fastest and safest: restore the config and restart. **No data migration occurred, so no
data rollback is normally required** — the namespace never changed (§3.1).

1. Restore server config and restart:
   ```bash
   cd /Users/gj/git/OpenViking-main
   cp ov.conf.bak-<stamp> ov.conf
   ```
   Removing `auth_mode` and `root_api_key` returns the server to auto-detected `dev`
   mode (`config.py:434-446`). `curl -s http://127.0.0.1:1933/health` should report
   `"auth_mode":"dev"`.
2. Restore client configs:
   ```bash
   cp ~/.openviking/ovcli.conf.bak-<stamp> ~/.openviking/ovcli.conf
   cp /Users/gj/.gemini/config/mcp_config.json.bak-<stamp> \
      /Users/gj/.gemini/config/mcp_config.json
   ```
3. Remove the `bot.ov_server` block if you added one, or restore the backed-up
   `ov.conf`.
4. If and only if data was damaged (unexpected — api_key mode only *adds* registry
   files under `data/viking/_system/` and `data/viking/default/_system/`):
   ```bash
   rm -rf /Users/gj/git/OpenViking-main/data/viking
   tar xzf data-viking-backup-<stamp>.tgz
   ```
   ⚠️ This deletes everything under `data/viking`. Verify the resolved absolute path is
   exactly `/Users/gj/git/OpenViking-main/data/viking` before running it.

**Partial rollback note:** the minted USER key lives in
`data/viking/default/_system/users.json`. Deleting just that file removes the credential
without touching memories, but it is unnecessary for a mode revert — dev mode ignores
those registries entirely.

---

## 9. Summary of corrections to the briefing

The briefing was materially accurate. Corrections and refinements:

1. **Config path**: the active server config is `/Users/gj/git/OpenViking-main/ov.conf`,
   not `~/.openviking/ov.conf`. Clients default to `~/.openviking/ov.conf`. This
   divergence is a real execution risk (§7 Step 0).
2. **`create_account("default")` will fail**, not succeed — the `default` account is
   auto-seeded by `FileStore.load()` (`file.py:169-180`) and creation rejects existing
   accounts (`file.py:420-421`, `file.py:1299-1301`). The correct bootstrap is
   **`register_user`**, not `create_account`.
3. **User creation returns the key** in the same response in `api_key` mode
   (`admin.py:1019-1021`) — no separate rotate call is needed.
4. **Disk path mapping confirmed**: `/local` → `<workspace>/viking`
   (`agfs_utils.py:401-417, 440-445`), so `ACCOUNTS_PATH=/local/_system/accounts.json` →
   `data/viking/_system/accounts.json`, and `viking://user/default` →
   `data/viking/default/user/default`. **Existing memories stay visible in api_key
   mode** because `_uri_to_path` keys purely on `account_id` + `user_id`
   (`_access.py:903-918`), both of which remain `default`/`default`.
5. **`/mcp` shares the resolver** — confirmed at `mcp_endpoint.py:190-220`; the MCP
   endpoint is equally subject to the ROOT-key trap.
6. **Anomaly resolved, and it matters**: `data/viking/_system/` is empty because the
   *running* server predates an **uncommitted** change to `dev.py` that makes dev mode
   build a real account store. The committed version is
   `app.state.api_key_manager = None`; the working tree replaces it with a real
   `APIKeyManager` + `await manager.load()` (`git diff openviking/server/auth/plugins/dev.py`),
   alongside `tests/server/test_dev_mode_admin_capability.py` and an ACL widening in
   `openviking/storage/acl.py:298-299`. **The running server is stale relative to the
   working tree** — coordinate with the agent making that change before restarting.
   The `api_key` bootstrap (§3.4, §7 Step 5) is unchanged either way.
7. **`ov` CLI fallback trap** (not in the briefing): without `api_key`, the CLI falls
   back to `root_api_key` for ordinary commands (`config.rs:369-375`) → 403.
8. **DSH plugin inference hazard** (not in the briefing): `authMode` is inferred as
   `trusted` whenever `account`/`user` are set (`credentials.mjs:287-290`), which is
   exactly the current `ovcli.conf`. Set the mode explicitly.
9. **Trusted mode is the only mode where the root key reaches data APIs** — worth
   stating plainly, since it is the deciding factor between the two targets.
10. **The running server is stale relative to the working tree** (see correction 6).
    Verify `git status` and coordinate before restarting; the dev-mode admin change is
    in flight and uncommitted.
