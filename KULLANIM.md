# irba test 1.0.0 — Kullanım kılavuzu

Android telefonları bilgisayardan tek tek veya toplu yönetmek için Türkçe masaüstü programı.
Laixi, Panda ve Zhifu programlarındaki özellikler tek programda toplandı.

## Kurulum
`irba_test_Setup_1.0.0.exe` dosyasını çalıştırın → İleri → Kur.
Program; masaüstü ve Başlat menüsü kısayolu ile kaldırma programını kurar.
İçinde scrcpy 5.0.1 ve ADB hazır gelir; başka bir şey kurmanız gerekmez.

## Telefonu bağlama
1. Telefonda **Ayarlar → Telefon hakkında → Yapım numarası**'na 7 kez dokunun.
2. **Geliştirici seçenekleri → USB hata ayıklama**'yı açın.
3. USB ile takın, telefondaki izni onaylayın.
4. Kablosuz için: cihazı seçip **Cihaz Bilgisi → Kablosuz bağlantıya geç**, ya da üstte
   IP yazıp **Bağlan**, ya da **Ağda telefon ara**. Android 11+ için **Eşleştir**.

## Bölümler
| Sekme | Ne yapar |
|---|---|
| **Sol panel** | Cihaz listesi, ad verme, **gruplar** (sağ tık veya “Gruplar” menüsü), **İşlem hedefi** (seçili / tümü / grup) |
| **Ekran** | Telefon ekranı program içinde, akıcı (60 kare/sn), ses, MP4 kayıt, Türkçe metin, pano, bildirim/ayar paneli, **Grup kontrol** |
| **Tüm Ekranlar** | Bütün telefonların küçük canlı görüntüleri; tıklayınca büyük ekranda açılır |
| **Toplu İşlemler** | ADB komutu, çoklu APK kurma, dosya gönderme, ekran görüntüsü, metin/pano gönderme, kablosuza geçirme, yeniden başlatma |
| **Uygulamalar** | Uygulama listesi, aç, durdur, verileri temizle, kaldır, APK'yı bilgisayara al (tek cihaz veya hedefin tamamı) |
| **Otomasyon** | Dokunuş/kaydırma/tuş/metin kaydı, düzenleme, kaydet/aç (.irba), hedefteki tüm cihazlarda tekrar tekrar oynatma |
| **Cihaz Bilgisi** | Marka, model, Android, pil, depolama, IP…; OTG modu (ADB'siz), ön/arka kamera yansıtma |
| **Ayarlar** | ADB yolu, ADB yeniden başlatma, sistem tepsisi, kayıt klasörü |

## İpuçları
- **Sürükle-bırak:** APK veya dosyaları pencereye bırakın → hedefteki cihazlara kurulur/gönderilir.
- **Ekranda:** sağ tık = Geri, orta tık = Ana ekran, Ctrl+V = bilgisayardaki metni (Türkçe dahil) yapıştırır.
- **Grup kontrol** açıkken ekrandaki tıklama/kaydırmalar hedefteki tüm telefonlara gider.
- Pencereyi kapatınca program **sistem tepsisinde** çalışmaya devam eder (Ayarlar'dan kapatılabilir).
- Laixi/Panda açıkken ADB çakışabilir → onları kapatıp **Ayarlar → ADB sunucusunu yeniden başlat**.
- Ekran görüntüleri ve videolar: `Videolar\irba test` klasörü.

## Bilinçli olarak eklenmeyenler
Sosyal medya/müzik platformlarında sahte etkileşim betikleri, otomatik root (Magisk),
bulut telefon kiralama, ödeme/lisans/reklam sistemleri.
