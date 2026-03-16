import gradio as gr
import subprocess
import os
import shutil
import re
import time
import signal
import psutil

# Aktif işlemi kontrol etmek için global değişken
aktif_islem = None

def on_kosul_testi():
    hatalar = []
    if shutil.which('ffmpeg') is None:
        hatalar.append("Kritik Hata: FFmpeg bulunamadı.")
    if shutil.which('ffprobe') is None:
        hatalar.append("Kritik Hata: FFprobe bulunamadı.")
    if not os.path.exists('/content/drive/MyDrive'):
        hatalar.append("Kritik Hata: Google Drive bağlı değil.")
    try:
        gpu_kontrol = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
        if gpu_kontrol.returncode != 0:
            hatalar.append("Kritik Hata: GPU algılanamadı. T4 GPU seçin.")
    except:
        hatalar.append("Kritik Hata: Donanım sürücüsü hatası.")
    return hatalar

def video_suresi_bul(dosya_yolu):
    komut = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', dosya_yolu]
    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=10)
        return float(sonuc.stdout.strip())
    except:
        return 0.0

def dosya_gecerli_mi(dosya_yolu):
    komut = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', dosya_yolu]
    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=15)
        return sonuc.returncode == 0 and len(sonuc.stdout.strip()) > 0
    except:
        return False

# Duraklatma ve Devam Ettirme Fonksiyonları
def islemi_duraklat():
    global aktif_islem
    if aktif_islem and aktif_islem.poll() is None:
        os.kill(aktif_islem.pid, signal.SIGSTOP)
        return "SİSTEM MESAJI: İşlem duraklatıldı. Ekran kartı beklemeye alındı."
    return "HATA: Çalışan aktif bir işlem bulunamadı."

def islemi_devam_ettir():
    global aktif_islem
    if aktif_islem and aktif_islem.poll() is None:
        os.kill(aktif_islem.pid, signal.SIGCONT)
        return "SİSTEM MESAJI: İşlem kaldığı yerden devam ediyor."
    return "HATA: Devam ettirilecek bir işlem bulunamadı."

def toplu_donustur(ana_klasor, cq_degeri, hiz_profili, ses_kalitesi):
    global aktif_islem
    altyapi_hatalari = on_kosul_testi()
    if altyapi_hatalari:
        yield "\n".join(altyapi_hatalari)
        return

    ham_dosyalar = os.listdir(ana_klasor)
    videolar = sorted([dosya for dosya in ham_dosyalar if dosya.lower().endswith(('.mkv', '.mp4')) and not dosya.startswith('HEVC_10BIT_')])
    
    if not videolar:
        yield "BİLGİ: Dönüştürülecek yeni video bulunamadı."
        return

    donusturulenler_klasoru = os.path.join(ana_klasor, "Converted")
    orijinaller_klasoru = os.path.join(ana_klasor, "Originals")
    os.makedirs(donusturulenler_klasoru, exist_ok=True)
    os.makedirs(orijinaller_klasoru, exist_ok=True)

    toplam_sayi = len(videolar)
    basarili_sayisi = 0
    hatali_sayisi = 0
    kurtarilan_alan_gb = 0.0
    genel_baslangic = time.time()
    rapor = f"SİSTEM: {toplam_sayi} video işlenecek.\n\n"

    for sira, video_adi in enumerate(videolar, 1):
        kaynak_dosya_yolu = os.path.join(ana_klasor, video_adi)
        dosya_adi_uzantisiz, _ = os.path.splitext(video_adi)
        yeni_video_adi = f"HEVC_10BIT_{dosya_adi_uzantisiz}.mkv"
        hedef_dosya_yolu = os.path.join(donusturulenler_klasoru, yeni_video_adi)

        if os.path.exists(hedef_dosya_yolu):
            continue

        sure_sn = video_suresi_bul(kaynak_dosya_yolu)
        guncel_durum = f"[{sira}/{toplam_sayi}] İşleniyor: {video_adi}"
        
        komut = [
            'ffmpeg', '-hwaccel', 'cuda', '-y', '-i', kaynak_dosya_yolu,
            '-map', '0', '-c:v', 'hevc_nvenc', '-preset', hiz_profili,
            '-tune', 'hq', '-multipass', 'fullres', '-spatial_aq', '1', '-temporal_aq', '1',
            '-rc', 'vbr_hq', '-cq', str(cq_degeri), '-b:v', '0', '-pix_fmt', 'p010le',
            '-c:a', 'libopus', '-b:a', ses_kalitesi, '-c:s', 'copy', hedef_dosya_yolu
        ]

        terminal_ciktisi = ""
        islem_baslangici = time.time()
        
        try:
            aktif_islem = subprocess.Popen(komut, stderr=subprocess.PIPE, text=True, universal_newlines=True)
            
            son_guncelleme = 0
            for satir in aktif_islem.stderr:
                terminal_ciktisi = (terminal_ciktisi + satir)[-500:]
                su_an = time.time()
                
                # İşlem duraklatılmışsa bu döngü yeni satır gelene kadar bekler
                fps_eslesme = re.search(r"fps=\s*([\d.]+)", satir)
                anlik_fps = fps_eslesme.group(1) if fps_eslesme else "0"

                if sure_sn > 0 and "time=" in satir:
                    eslesme = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", satir)
                    if eslesme:
                        saat, dk, sn = float(eslesme.group(1)), float(eslesme.group(2)), float(eslesme.group(3))
                        gecen_video = saat * 3600 + dk * 60 + sn
                        yuzde = min(100.0, (gecen_video / sure_sn) * 100)
                        
                        if su_an - son_guncelleme > 3:
                            gecen_gercek = su_an - islem_baslangici
                            kalan_sn = int((gecen_gercek / (yuzde / 100)) - gecen_gercek) if yuzde > 0 else 0
                            kalan_dk, kalan_sn_mod = divmod(kalan_sn, 60)
                            yield rapor + guncel_durum + f" %{yuzde:.1f} | Kalan: {kalan_dk}dk {kalan_sn_mod}sn | Hız: {anlik_fps} fps"
                            son_guncelleme = su_an
                
            aktif_islem.wait(timeout=3600)

            if aktif_islem.returncode == 0 and dosya_gecerli_mi(hedef_dosya_yolu):
                basarili_sayisi += 1
                orij_boyut = os.path.getsize(kaynak_dosya_yolu) / (1024**3)
                yeni_boyut = os.path.getsize(hedef_dosya_yolu) / (1024**3)
                kurtarilan_alan_gb += (orij_boyut - yeni_boyut)
                shutil.move(kaynak_dosya_yolu, os.path.join(orijinaller_klasoru, video_adi))
                rapor += f"[{sira}/{toplam_sayi}] TAMAMLANDI: {video_adi}\n"
            else:
                if os.path.exists(hedef_dosya_yolu): os.remove(hedef_dosya_yolu)
                hatali_sayisi += 1
                rapor += f"[{sira}/{toplam_sayi}] HATA: {video_adi}\n"
        except Exception as e:
            if os.path.exists(hedef_dosya_yolu): os.remove(hedef_dosya_yolu)
            hatali_sayisi += 1
            rapor += f"[{sira}/{toplam_sayi}] SİSTEM HATASI: {str(e)}\n"
            
        yield rapor 

    yield rapor + f"\nİŞLEM BİTTİ. Toplam {kurtarilan_alan_gb:.2f} GB alan kazanıldı."

# Arayüz Oluşturma
with gr.Blocks(theme=gr.themes.Soft()) as arayuz:
    gr.Markdown("### Otonom Video İşleyici (Duraklat/Devam Et Destekli)")
    
    with gr.Row():
        klasor_girdisi = gr.Textbox(label="Klasör Yolu", placeholder="/content/drive/MyDrive/Videolar", scale=3)
        baslat_butonu = gr.Button("Sistemi Başlat", variant="primary", scale=1)
        
    with gr.Row():
        duraklat_butonu = gr.Button("⏸ Duraklat", variant="secondary")
        devam_butonu = gr.Button("▶ Devam Et", variant="secondary")

    with gr.Accordion("Ayarlar", open=False):
        with gr.Row():
            cq_ayari = gr.Slider(18, 32, value=24, step=1, label="Kalite (CQ)")
            hiz_ayari = gr.Dropdown(["slow", "medium", "fast"], value="slow", label="Hız")
            ses_ayari = gr.Dropdown(["128k", "160k", "192k"], value="128k", label="Ses (Opus)")

    islem_ekrani = gr.Textbox(label="Monitör", max_lines=20, interactive=False)

    baslat_butonu.click(fn=toplu_donustur, inputs=[klasor_girdisi, cq_ayari, hiz_ayari, ses_ayari], outputs=islem_ekrani)
    duraklat_butonu.click(fn=islemi_duraklat, outputs=islem_ekrani)
    devam_butonu.click(fn=islemi_devam_ettir, outputs=islem_ekrani)

arayuz.queue().launch(share=True, debug=True)
