# =============================================================================
# businessfind — virtual environment setup and run targets
#
# PORTABILITY NOTE
# This Makefile works under Git Bash / Linux / macOS (sh) and under
# PowerShell / cmd. Two rules make that possible:
#   1) ALWAYS use forward slashes in paths. A backslash works in cmd but sh
#      treats it as an escape character and swallows it.
#   2) NEVER use shell built-ins (test, cp, rm, if) or shell redirection (>).
#      cmd does not have those commands, and redirection combined with a
#      forward-slash path makes cmd treat the first path segment as a command.
#      File operations go through Python; existence checks use make's own
#      file-target mechanism.
#
# Quick start:
#   make setup      -> create the venv, install dependencies, prepare .env
#   make check      -> smoke test that needs no network and no API keys
#   make run ARGS=<arguments in double quotes>
#
# Note: when running from PowerShell, pass ARGS in double quotes.
# =============================================================================

VENV    ?= .venv
PYTHON  ?= python
ARGS    ?=

ifeq ($(OS),Windows_NT)
    VENV_PY  := $(VENV)/Scripts/python.exe
    ACT_HINT := $(VENV)\Scripts\activate
else
    VENV_PY  := $(VENV)/bin/python
    ACT_HINT := source $(VENV)/bin/activate
endif

STAMP := $(VENV)/.install-stamp
CLI   := $(VENV_PY) -m src.cli

# Helper for printing example commands with double quotes intact.
#
# Two traps here:
#  1) Printing quotes with a plain echo: cmd prints them literally, sh strips
#     them — so the example is wrong in one of the two shells. Fix: never show
#     the quote to the shell; have Python emit it via chr(34).
#  2) Grouping arguments with single quotes: sh groups them, but cmd does not
#     treat a single quote as quoting and splits on spaces. Fix: no argument
#     may contain a space; use ~ instead and let Python convert it back.
QUOTE_ECHO = $(PYTHON) -c "import sys; print(' '*3 + sys.argv[1].replace(chr(126),' ') + chr(34) + sys.argv[2].replace(chr(126),' ') + chr(34))"

.DEFAULT_GOAL := help
.PHONY: help setup venv install reinstall env run list-types dry-run probe \
        check freeze clean distclean

# -----------------------------------------------------------------------------
help:
	@echo =============================================================
	@echo  businessfind - available commands
	@echo =============================================================
	@echo  make setup       create venv + install dependencies + prepare .env
	@echo  make venv        create the virtual environment only
	@echo  make install     install dependencies into the venv
	@echo  make reinstall   force a dependency reinstall
	@echo  make env         create .env from .env.example if missing
	@echo  -------------------------------------------------------------
	@echo  make check       smoke test - needs no network and no API keys
	@echo  make list-types  list the supported business types
	@echo  make dry-run     plan + quota estimate without any request
	@echo  make probe       TomTom category sanity check - needs a key
	@echo  make run         run a scan
	@echo  -------------------------------------------------------------
	@echo  make freeze      write installed versions to requirements.lock.txt
	@echo  make clean       remove __pycache__ directories
	@echo  make distclean   remove the virtual environment
	@echo =============================================================
	@echo  Pass arguments with ARGS, for example:
	@$(QUOTE_ECHO) make~run~ARGS= --address~Kadikoy,~Istanbul~--radius~2000
	@echo  To activate the virtual environment manually:
	@echo    $(ACT_HINT)
	@echo =============================================================

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

setup: install env
	@echo -------------------------------------------------------------
	@echo  Setup complete. Next, fill in the .env file:
	@echo    CONTACT_EMAIL, TOMTOM_API_KEY, LANGSEARCH_API_KEY
	@echo  To try it without any API key:
	@$(QUOTE_ECHO) make~run~ARGS= --address~Kadikoy~--skip-tomtom~--skip-langsearch~--contact~you@example.com
	@echo -------------------------------------------------------------

venv: $(VENV_PY)

# File target: the venv is created when absent and left alone when present.
# That is why no shell "if" is needed.
$(VENV_PY):
	@echo [*] Creating virtual environment: $(VENV)
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip

install: $(STAMP)

# The stamp file depends on requirements.txt and on the venv: if either is
# newer, dependencies are reinstalled; otherwise the step is skipped.
$(STAMP): requirements.txt $(VENV_PY)
	@echo [*] Installing dependencies from requirements.txt
	$(VENV_PY) -m pip install -r requirements.txt
	@$(PYTHON) -c "open('$(STAMP)','w').close()"
	@echo [+] Installation complete

reinstall:
	@$(PYTHON) -c "import os; os.path.exists('$(STAMP)') and os.remove('$(STAMP)')"
	@$(MAKE) install

# .env is a file target and DELIBERATELY has NO prerequisites.
#
# Writing ".env: .env.example" here is tempting but DANGEROUS: when the
# timestamp of .env.example is refreshed (after a "git pull", for example),
# make considers .env stale, regenerates it, and OVERWRITES YOUR REAL KEYS.
# A prerequisite-free file target only runs when the file does not exist yet.
env: .env

.env:
	@echo [*] .env not found, copying .env.example
	@$(PYTHON) -c "import shutil; shutil.copyfile('.env.example','.env')"
	@echo [!] .env created - a real scan will not run until you fill it in

# -----------------------------------------------------------------------------
# Running
# -----------------------------------------------------------------------------

run: $(STAMP)
	$(CLI) $(ARGS)

list-types: $(STAMP)
	$(CLI) --list-types

dry-run: $(STAMP)
	$(CLI) --dry-run $(ARGS)

probe: $(STAMP)
	$(CLI) --tomtom-probe $(ARGS)

# Smoke test that needs neither network access nor an API key.
# It answers: does the package import, is the CLI alive, is a plan produced?
#
# Shell redirection (>) is DELIBERATELY avoided: under cmd, combined with a
# forward-slash path it makes cmd treat the first path segment as a command.
# Output is therefore captured inside Python - portable and with no temp file.
check: $(STAMP)
	@echo [*] 1/3 compiling all modules
	$(VENV_PY) -m compileall -q src
	@echo [*] 2/3 importing the package and validating the type dictionary
	@$(VENV_PY) -c "from src.osm_source import BUSINESS_TYPES as B, TYPE_ALIASES as A; assert len(B) > 40; print('[+] business types:', len(B), '- aliases:', len(A))"
	@echo [*] 3/3 producing a dry-run plan
	@$(VENV_PY) -c "import io,sys; from src import cli; b=io.StringIO(); o=sys.stdout; sys.stdout=b; rc=cli.main(['--dry-run','--latlng','40.9903,29.0270','--radius','700','--types','restaurant','hair_salon']); sys.stdout=o; t=b.getvalue(); assert rc==0 and 'DRY RUN' in t and 'Overpass' in t, 'dry-run did not produce the expected output'; print('[+] dry-run plan produced - no HTTP request was made')"
	@echo [+] Smoke test passed

# No shell redirection here either - see the note on the `check` target.
freeze: $(STAMP)
	@$(VENV_PY) -c "import subprocess,sys; out=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True).stdout; open('requirements.lock.txt','w',encoding='utf-8').write(out); print('[+] requirements.lock.txt written -', len(out.splitlines()), 'packages')"

# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------

# Generated CSV / NOTES files are INTENTIONALLY left alone - they are work output.
clean:
	@$(PYTHON) -c "import pathlib,shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	@echo [+] __pycache__ directories removed

distclean: clean
	@$(PYTHON) -c "import shutil; shutil.rmtree('$(VENV)', ignore_errors=True)"
	@echo [+] $(VENV) removed - .env and output files were preserved
