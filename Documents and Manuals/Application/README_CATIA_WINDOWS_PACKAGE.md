# RamAir CATIA V5 package for Windows

This package contains the current Python preprocessor, the main CATScript,
profiles, editable JSON configuration and a pre-generated `CATIA_inputs`
folder. It does not contain or install CATIA V5.

## First use on a Windows PC

1. Extract the complete `RamAir_CATIA_Windows` folder to a short writable path,
   for example `C:\RamAir_CATIA_Windows`. Do not run it from inside the ZIP.
2. Install 64-bit Python 3.10 or newer with the Windows `py.exe` launcher.
3. Run `SETUP_CATIA_PREPROCESSOR_WINDOWS.bat` once. It creates `.venv-catia`
   and installs NumPy, pandas and Matplotlib without forcing a pip upgrade.
4. Edit `configs\default_case_config.json` and the files under `profiles\` as
   required. Every profile reference is relative to the extracted package.
   The preprocessor also recovers a relocated profile by its filename from
   `profiles\`, so a stale path from another Windows account is not used.
   `configs\last_preprocessor_run_config.json` is updated only after a complete
   successful preprocessor run.
5. Run `RUN_CATIA_PREPROCESSOR_WINDOWS.bat`. It regenerates and verifies
   `CATIA_inputs`, then opens that folder.
   To use another editable JSON, pass it as the first argument:
   `RUN_CATIA_PREPROCESSOR_WINDOWS.bat configs\my_canopy.json`.
6. In CATIA V5, run `Generate_RamAir_Canopy_MAIN.CATScript`. The macro first
   reads `RAMAIR_CATIA_INPUTS`, then looks for `CATIA_inputs` in CATIA's current
   directory, and finally asks for the folder interactively.

To remove path ambiguity before launching CATIA, set a user environment
variable named `RAMAIR_CATIA_INPUTS` to the full extracted
`C:\...\RamAir_CATIA_Windows\CATIA_inputs` path.

## Folder contract

- `profiles`: source `.csv` and `.dat` profiles.
- `configs`: editable geometry and optional-system configuration.
- `configs\last_preprocessor_run_config.json`: exact configuration from the
  latest successful run. The BAT uses `configs\default_case_config.json`
  unless another JSON is supplied explicitly.
- `CATIA_inputs`: generated files read by the CATScript only.
- `CATIA_exports`: intended CAD export destination.
- `logs`: preprocessor/package logs.
- `reports`: static package verification.
- `CFD_2D\scripts\ramair_profile_utils.py`: the only shared Python helper
  required by this standalone preprocessor.

`VERIFY_CATIA_PACKAGE.py` performs static checks only. It does not launch CATIA.
The ZIP manifest includes SHA-256 hashes for the critical source and generated
files.

## Regenerating the ZIP

From the full development project:

```powershell
python "Application Support\Tools\package_ramair_catia_windows.py"
```

The verified archive is written under `Application Support\Packages\`.
If the development runtime has no saved last-run file, the packager uses its
active `default_case_config.json` instead of failing.
