# Minnak

Windows için geliştirilmiş, 30 FPS fizik motorlu ve yerel Ollama yapay zeka destekli piksel masaüstü kedisi. Pencerelerin üstüne zıplayıp tırmanır, açık pencereleri/oyunları izler, sohbet eder ve SQLite + Markdown tabanlı kalıcı hafıza tutar.

## Özellikler

- **Masaüstü Fiziği & Piksel Sanat:** 30 FPS balistik zıplama, pencere kenarına tırmanma, pencere taşınırken kayma, köşeden sarkma, imleçten kaçma/pati atma ve 4 renk paleti (`PyQt6` + Win32 DWM).
- **Oyun Modu (Hayalet Modu):** Tam ekran oyun veya yüksek GPU yükü algılandığında VRAM'i tahliye eder, Ollama önceliğini düşürür ve kedi yarı saydam tıklama geçirgen (click-through) moda geçer.
- **Yerel Yapay Zeka & Ekran Algısı:** Ollama (`gemma4:e4b`) üzerinden tek cümlelik sohbet, gerektiğinde düşük çözünürlüklü ekran görüntüsü analizi (şifre/banka pencereleri hariç) ve DuckDuckGo açık web araması.
- **Sıfır Maliyetli Yaşam Döngüsü:** AFK, medya çalma, sistem stresi, uzun odaklanma ve gece saatini algılar; yalnız ilgili olaylarda LLM çalıştırır.
- **Hafıza & Günlük:** Konuşma geçmişi, 5-harf kök çağrışım araması, otomatik/manuel kurallar (`ogrendiklerim.md`) ve doğal günlük (`gunluk.md`).

## Gereksinimler

- Windows 10 / 11
- Python 3.11+ (3.13 önerilir)
- [Ollama](https://ollama.com/) ve yerel model:
  ```powershell
  ollama pull gemma4:e4b
  ```

## Kurulum ve Çalıştırma

```powershell
git clone https://github.com/zkaans/Minnak.git
cd Minnak
pip install -r requirements.txt
python main.py
```

Diğer başlatma seçenekleri:

- `python main.py --demo` — Başlangıçta zıplama ve yürüme demosunu oynatır.
- `pythonw.exe main.py` — Konsol penceresi açmadan başlatır.
- `$env:MINNAK_USER="Adınız"; python main.py` — Prompt ve günlüklerde kullanılacak kullanıcı adını belirler (varsayılan: `kullanıcı`).

## Kontroller

- **`Alt + M`** (veya kediye tek tık): Sohbet kutusunu aç/kapat.
- **Sürükle & Bırak:** Kediyi tutup taşı veya fırlat.
- **Çift Tık:** Kediyi olduğu yere sabitle (Pin).
- **Sağ Tık / Sistem Tepsisi:** Kontrol Merkezi, Günlük Okuyucu, Kedi Görünümü, Sabitleme, Hayalet Modu ve Animasyon Debug menüleri.

## Test ve Mimari

```powershell
python test_core.py
```

Katman yapısı ve geliştirici kuralları için [ARCHITECTURE.md](ARCHITECTURE.md) dosyasına bakın.

## Lisans

Bu proje **GNU General Public License v3.0 (GPL-3.0)** ile lisanslanmıştır. Ayrıntılar için [LICENSE](LICENSE) dosyasına bakın.
