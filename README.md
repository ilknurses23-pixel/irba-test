# irba test

Android telefonları bilgisayardan tek tek veya toplu yönetmek için Türkçe Windows masaüstü programı.

- Solda tek kontrol paneli, ortada tüm telefonların canlı ekranları
- Gruplar, numaralı hızlı seçim, seçili cihazları birlikte kontrol
- Toplu APK kurma, dosya gönderme, ADB komutları, uygulama yöneticisi
- Otomasyon (makro kaydet / oynat), Türkçe metin ve pano paylaşımı
- Program içi **Güncelle** düğmesi

## Kurulum
`guncelleme/` klasöründeki `irba_test_Setup_<sürüm>.exe` dosyasını indirip çalıştırın.
Kurulduktan sonra yeni sürümler programdaki **Güncelle** düğmesiyle gelir.

## Güncelleme nasıl çalışır
Program `guncelleme/guncelleme.json` dosyasını okur. Sürüm daha yeniyse kurulum dosyasını indirir,
SHA-256 özetini doğrular, sessizce kurar ve programı yeniden açar.

## Geliştirme
- Python 3.13 + tkinter (ek paket gerekmez)
- Ekran motoru: [scrcpy](https://github.com/Genymobile/scrcpy) 5.0.1 (Apache 2.0) — `tools/scrcpy`
- Paketleme: PyInstaller + Inno Setup 6 (`packaging/yayinla.ps1`)
