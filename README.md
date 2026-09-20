## Running the Application

 > **Note:** The Windows executable setup is currently available for the `demo` branch.

 ### Option 1: Run from Python

 First, switch to the `windows-executable` branch:

```
git switch windows-executable
```

 Install the project dependencies using `uv`:

```
uv sync
```

 Start the FastAPI application:

```
uv run uvicorn app.main:app --reload
```

 The application will be available at:

```
http://127.0.0.1:8000
```

 Open that address in your web browser.

 ### Option 2: Run the Windows Executable

 The standalone Windows executable is configured on the `windows-executable` branch.

 After switching to the branch:

```
git switch windows-executable
```

 Build the executable:

```
uv run pyinstaller --clean DigitalHorizonScan.spec
```

 The executable will be generated in:

```
dist/DigitalHorizonScan/DigitalHorizonScan.exe
```

 You can run it from Windows by double-clicking:

```
DigitalHorizonScan.exe
```

 Alternatively, run it from Command Prompt:

```
cd dist\DigitalHorizonScan
DigitalHorizonScan.exe
```

 Once the application starts, open the following address in your browser:

```
http://127.0.0.1:8000
```

 The following files are required to reproduce the Windows executable:

```
run.py
DigitalHorizonScan.spec
pyproject.toml
uv.lock
```

 The executable runs the application as a local web server on port `8000`. It is not a traditional desktop GUI application.
