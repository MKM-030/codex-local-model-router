# Maintenance

Finish active tasks before updating the router or Codex configuration. Updating is separate from starting a model server.

## Update an existing installation

Download or clone the current source, inspect it, then run:

```powershell
.\Update-CodexToolBridge.ps1
```

The updater recognizes both the installer-managed `~/.codex/local-model-router/` layout and the original root-level `~/.codex/hybrid-model-router.py` layout. It creates backups and normally restarts the router belonging to that installation. It does not restart Codex or the model server. Restart Codex afterward, once active tasks have finished, to reload tool and model metadata.

`-NoStart` updates the files without restarting the router. Those code changes are not active until the router is restarted.

## Repair a model display name

```powershell
.\Update-CodexToolBridge.ps1 `
    -ModelId "Qwen3.8-Flash-Next" `
    -DisplayName "Qwen3.8 Flash Next - Local"
```

This changes the matching display label, not its routing ID. It is still an update operation and may restart the router. Use `-NoStart` to avoid that while preparing the change.

## Enable cloud web search

```powershell
.\Update-CodexToolBridge.ps1 -AllowCloudSearch -SearchModel "YOUR_AUTHORIZED_CLOUD_MODEL_ID"
```

Search sends search commands and recent Codex conversation context to the cloud search backend. Use a cloud model your account is authorized to access. This setting does not redirect local inference to that cloud model.

## Rollback

Standard installs include an uninstaller and saved rollback state. For an original root-level installation, use the timestamped updater backup to restore the prior router, configuration, catalog and startup entry. Do not delete the entire `.codex` directory.

## Developer checks

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tests\install_roundtrip.py
powershell.exe -NoProfile -File tests\Test-InstallerPreflight.ps1
python tests\install_without_backend.py
```

The no-backend lifecycle test uses disposable directories and temporary ports; it does not load model weights or touch your normal Codex configuration. It starts only its own test router and a small mock HTTP backend, and closes only those owned processes/resources.

See [validation](VALIDATION.md) for historical execution evidence. New tests must be run before claiming that a new installation path has been validated on Windows.
