# =============================================================================
# leadgen — venv kurulumu ve calistirma
#
# TASINABILIRLIK NOTU
# Bu Makefile hem Git Bash/Linux/macOS (sh) hem de PowerShell/cmd altinda
# calisir. Bunun icin iki kurala uyulur:
#   1) Yollarda HER ZAMAN duz egik cizgi (/) kullanilir. Ters egik cizgi
#      cmd'de calisir ama sh onu kacis karakteri sayip yutar.
#   2) Kabuk komutu (test, cp, rm, if) ve kabuk yonlendirmesi (>) KULLANILMAZ.
#      cmd'de bu komutlar yok; yonlendirme ise duz egik cizgili yolla
#      birlesince cmd'yi yolun ilk parcasini komut sanmaya itiyor.
#      Dosya islemleri Python ile, varlik kontrolleri make'in kendi
#      dosya-hedefi mekanizmasiyla yapilir.
#
# Hizli baslangic:
#   make setup      -> venv olustur + bagimliliklari kur + .env hazirla
#   make check      -> ag/anahtar gerektirmeyen duman testi
#   make run ARGS=<parametreler cift tirnak icinde>
#
# Not: PowerShell'den calistirirken ARGS degerini cift tirnakla verin.
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
CLI   := $(VENV_PY) -m leadgen.cli

# Ornek komutlari cift tirnakli gostermek icin yardimci.
#
# Iki tuzak var:
#  1) Duz echo ile tirnak basmak: cmd tirnaklari harfiyen yazar, sh yutar —
#     ornek iki kabuktan birinde yanlis kopyalanir. Cozum: tirnagi kabuga hic
#     gostermeyip Python'a chr(34) ile urettirmek.
#  2) Argumanlari tek tirnakla gruplamak: sh gruplar ama cmd tek tirnagi
#     quoting saymaz, bosluktan boler. Cozum: argumanlarda BOSLUK KULLANMAMAK;
#     bosluk yerine ~ konur, Python geri cevirir.
QUOTE_ECHO = $(PYTHON) -c "import sys; print(' '*3 + sys.argv[1].replace(chr(126),' ') + chr(34) + sys.argv[2].replace(chr(126),' ') + chr(34))"

.DEFAULT_GOAL := help
.PHONY: help setup venv install reinstall env run list-types dry-run probe \
        check freeze clean distclean

# -----------------------------------------------------------------------------
help:
	@echo =============================================================
	@echo  leadgen - kullanilabilir komutlar
	@echo =============================================================
	@echo  make setup       venv olustur + bagimliliklari kur + .env hazirla
	@echo  make venv        sadece sanal ortami olustur
	@echo  make install     bagimliliklari venv icine kur
	@echo  make reinstall   bagimliliklari zorla yeniden kur
	@echo  make env         .env yoksa .env.example dosyasindan olustur
	@echo  -------------------------------------------------------------
	@echo  make check       duman testi - ag ve API anahtari gerektirmez
	@echo  make list-types  desteklenen is tiplerini listele
	@echo  make dry-run     istek atmadan plan + kota tahmini
	@echo  make probe       TomTom kategori kalite kontrolu - anahtar gerekir
	@echo  make run         taramayi calistir
	@echo  -------------------------------------------------------------
	@echo  make freeze      kurulu surumleri requirements.lock.txt icine yaz
	@echo  make clean       __pycache__ klasorlerini sil
	@echo  make distclean   sanal ortami tamamen sil
	@echo =============================================================
	@echo  ARGS ile parametre gecin, ornek:
	@$(QUOTE_ECHO) make~run~ARGS= --address~Kadikoy,~Istanbul~--radius~2000
	@echo  Sanal ortami elle etkinlestirmek icin:
	@echo    $(ACT_HINT)
	@echo =============================================================

# -----------------------------------------------------------------------------
# Kurulum
# -----------------------------------------------------------------------------

setup: install env
	@echo -------------------------------------------------------------
	@echo  Kurulum tamam. Sirada .env dosyasini doldurmak var:
	@echo    CONTACT_EMAIL, TOMTOM_API_KEY, LANGSEARCH_API_KEY
	@echo  Anahtarsiz denemek icin:
	@$(QUOTE_ECHO) make~run~ARGS= --address~Kadikoy~--skip-tomtom~--skip-langsearch~--contact~siz@ornek.com
	@echo -------------------------------------------------------------

venv: $(VENV_PY)

# Dosya hedefi: venv yoksa olusturulur, varsa dokunulmaz.
# Kabuk "if" gerekmemesinin sebebi bu.
$(VENV_PY):
	@echo [*] Sanal ortam olusturuluyor: $(VENV)
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip

install: $(STAMP)

# Damga dosyasi requirements.txt ve venv'e bagli: ikisinden biri
# yenilenirse kurulum tekrarlanir, aksi halde atlanir.
$(STAMP): requirements.txt $(VENV_PY)
	@echo [*] Bagimliliklar kuruluyor: requirements.txt
	$(VENV_PY) -m pip install -r requirements.txt
	@$(PYTHON) -c "open('$(STAMP)','w').close()"
	@echo [+] Kurulum tamamlandi

reinstall:
	@$(PYTHON) -c "import os; os.path.exists('$(STAMP)') and os.remove('$(STAMP)')"
	@$(MAKE) install

# .env bir dosya hedefidir ve BILEREK HICBIR ON KOSULU YOKTUR.
#
# Buraya ".env: .env.example" yazmak cazip gelir ama TEHLIKELIDIR:
# .env.example'in tarihi yenilenince (ornegin bir "git pull" sonrasi) make
# .env'i "eski" sayip yeniden uretir ve GERCEK ANAHTARLARIN uzerine yazar.
# On kosulsuz dosya hedefi ise yalnizca dosya HIC YOKKEN calisir.
env: .env

.env:
	@echo [*] .env bulunamadi, .env.example kopyalaniyor
	@$(PYTHON) -c "import shutil; shutil.copyfile('.env.example','.env')"
	@echo [!] .env olusturuldu - icini doldurmadan gercek tarama calismaz

# -----------------------------------------------------------------------------
# Calistirma
# -----------------------------------------------------------------------------

run: $(STAMP)
	$(CLI) $(ARGS)

list-types: $(STAMP)
	$(CLI) --list-types

dry-run: $(STAMP)
	$(CLI) --dry-run $(ARGS)

probe: $(STAMP)
	$(CLI) --tomtom-probe $(ARGS)

# Ag ve API anahtari gerektirmeyen duman testi.
# Amaci: paket import ediliyor mu, CLI ayakta mi, plan uretiliyor mu.
#
# Kabuk yonlendirmesi (>) BILEREK kullanilmaz: cmd altinda duz egik cizgili
# yol ile birlesince cmd yolun ilk parcasini komut sanip patliyor. Ciktilar
# bu yuzden Python icinde yakalanir - hem tasinabilir hem gecici dosyasiz.
check: $(STAMP)
	@echo [*] 1/3 tum moduller derleniyor
	$(VENV_PY) -m compileall -q leadgen
	@echo [*] 2/3 paket import edilip is tipi sozlugu dogrulaniyor
	@$(VENV_PY) -c "from leadgen.osm_source import BUSINESS_TYPES as B, TYPE_ALIASES as A; assert len(B) > 40; print('[+] is tipi:', len(B), '- takma ad:', len(A))"
	@echo [*] 3/3 dry-run plani uretiliyor
	@$(VENV_PY) -c "import io,sys; from leadgen import cli; b=io.StringIO(); o=sys.stdout; sys.stdout=b; rc=cli.main(['--dry-run','--latlng','40.9903,29.0270','--radius','700','--types','restoran','kuafor']); sys.stdout=o; t=b.getvalue(); assert rc==0 and 'DRY RUN' in t and 'Overpass' in t, 'dry-run beklenen ciktiyi vermedi'; print('[+] dry-run plani uretildi - HTTP istegi atilmadi')"
	@echo [+] Duman testi gecti

# Burada da kabuk yonlendirmesi yok - sebebi `check` hedefindeki not.
freeze: $(STAMP)
	@$(VENV_PY) -c "import subprocess,sys; out=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True).stdout; open('requirements.lock.txt','w',encoding='utf-8').write(out); print('[+] requirements.lock.txt yazildi -', len(out.splitlines()), 'paket')"

# -----------------------------------------------------------------------------
# Temizlik
# -----------------------------------------------------------------------------

# Uretilen CSV/NOTLAR dosyalarina KASITLI olarak dokunulmaz - onlar is ciktisi.
clean:
	@$(PYTHON) -c "import pathlib,shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	@echo [+] __pycache__ klasorleri silindi

distclean: clean
	@$(PYTHON) -c "import shutil; shutil.rmtree('$(VENV)', ignore_errors=True)"
	@echo [+] $(VENV) silindi - .env ve cikti dosyalari korundu
