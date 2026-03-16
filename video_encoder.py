import gradio as gr
import subprocess
import os
import shutil
import re
import time
import signal

# İşlem kontrolü için global değişken
aktif_islem = None

def on_kosul_testi():
    hatalar = []
    if shutil.which('ffmpeg') is None: hatalar.append("Kritik Hata: FFmpeg bulunamadı.")
    if shutil.which('ffprobe') is None: hatalar.append("Kritik Hata: FFprobe bulunamadı.")
    if not os.path.exists('/content/drive/MyDrive'): hatalar.append("Kritik Hata: Drive bağlı değil.")
    try:
        gpu = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
        if gpu.returncode != 0: hatalar.append("Kritik Hata: GPU algılanamadı.")
    except: hatalar.append("Kritik Hata: Donanım hatası.")
    return hatalar

def video_suresi_bul(dosya_yolu):
    komut = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', dosya_yolu]
    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=10)
        return float(sonuc.stdout.strip())
    except: return 0.0

def dosya_gecerli_mi(dosya_yolu):
    komut = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', dosya_yolu]
    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=15)
        return sonuc.returncode == 0 and len(sonuc.stdout.strip()) > 0
    except: return False

def islemi_durdur():
    global aktif_islem
    if aktif_islem and aktif_islem.poll() is None:
        os.kill(aktif_islem.pid, signal.SIGSTOP)
        return "⏸ SİSTEM: İşlem donduruldu."
    return "⚠️ HATA: Çalışan aktif işlem yok."

def islemi_devam_ettir():
    global aktif_islem
    if aktif_islem and aktif_islem.poll() is None:
        os.kill(aktif_islem.pid, signal.SIGCONT)
        return "▶ SİSTEM: İşlem devam ediyor."
    return "⚠️ HATA: Devam edecek işlem yok."

def toplu_donustur(ana_klasor, cq_degeri, hiz_profili, ses_kalitesi):
    global aktif_islem
    hatalar = on_kosul_testi()
    if hatalar:
        yield "\n".join(hatalar)
        return

    videolar = sorted([f for f in os.listdir(ana_klasor) if f.lower().endswith(('.mkv', '.mp4')) and not f.startswith('HEVC_10BIT_')])
    if not videolar:
        yield "BİLGİ: İşlenecek yeni video bulunamadı."
        return

    donusturulenler = os.path.join(ana_klasor, "Converted")
    orijinaller = os.path.join(ana_klasor, "Originals")
    os.makedirs(donusturulenler, exist_ok=True)
    os.makedirs(orijinaller, exist_ok=True)

    basarili, hatali, tasarruf = 0, 0, 0.0
    genel_bas = time.time()
    rapor = f"🚀 {len(videolar)} video işlenecek.\n\n"

    for sira, video_adi in enumerate(videolar, 1):
        kaynak = os.path.join(ana_klasor, video_adi)
        temiz_ad = re.sub(r'[:*?"<>|]', '-', video_adi)
        hedef = os.path.join(donusturulenler, f"HEVC_10BIT_{os.path.splitext(temiz_ad)[0]}.mkv")

        if os.path.exists(hedef): continue

        sure = video_suresi_bul(kaynak)
        durum_baslik = f"[{sira}/{len(videolar)}] İşleniyor: {video_adi}"
        
        komut = [
            'ffmpeg', '-hwaccel', 'cuda', '-y', '-i', kaynak,
            '-map', '0', '-map_metadata', '0',
            '-c:v', 'hevc_nvenc', '-preset', hiz_profili,
            '-tune', 'hq', '-rc', 'vbr', '-cq', str(cq_degeri), '-b:v', '0',
            '-pix_fmt', 'p010le',
            '-c:a', 'libopus', '-b:a', ses_kalitesi, '-ac', '2',
            '-c:s', 'copy', '-c:t', 'copy',
            hedef
        ]

        f_log = []
        try:
            aktif_islem = subprocess.Popen(komut, stderr=subprocess.PIPE, text=True, universal_newlines=True)
            son_upd = 0
            for satir in aktif_islem.stderr:
                f_log.append(satir)
                if len(f_log) > 10: f_log.pop(0)
                if sure > 0 and "time=" in satir:
                    eslesme = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", satir)
                    if eslesme:
                        saat, dk, sn = float(eslesme.group(1)), float(eslesme.group(2)), float(eslesme.group(3))
                        yuzde = ((saat * 3600 + dk * 60 + sn) / sure) * 100
                        if time.time() - son_upd > 3:
                            yield rapor + durum_baslik + f" %{yuzde:.1f}"
                            son_upd = time.time()
            
            aktif_islem.wait()

            if aktif_islem.returncode == 0:
                # --- DRIVE SENKRONİZASYON VE DOĞRULAMA ADIMI ---
                yield rapor + durum_baslik + " %100 | ⏳ Dosya Drive'a yazılıyor, lütfen bekleyin..."
                
                # Bellekteki veriyi diske/Drive'a fiziksel olarak yazmaya zorla
                os.sync() 
                
                # Yazma işleminin bitip bitmediğini küçük bir gecikmeyle kontrol et
                time.sleep(2) 

                if dosya_gecerli_mi(hedef):
                    basarili += 1
                    o_boyut = os.path.getsize(kaynak) / (1024**3)
                    y_boyut = os.path.getsize(hedef) / (1024**3)
                    tasarruf += (o_boyut - y_boyut)
                    
                    yield rapor + durum_baslik + " %100 | 📦 Orijinal dosya yedekleniyor..."
                    shutil.move(kaynak, os.path.join(orijinaller, video_adi))
                    os.sync() # Taşıma işlemini de doğrula
                    
                    rapor += f"✅ TAMAMLANDI: {video_adi}\n   ↳ {o_boyut:.2f}GB -> {y_boyut:.2f}GB\n"
                else:
                    raise Exception("Dosya yazıldı ancak bütünlük kontrolünden geçemedi.")
            else:
                if os.path.exists(hedef): os.remove(hedef)
                hatali += 1
                rapor += f"❌ HATA: {video_adi}\n↳ Log: {''.join(f_log).strip()}\n"
        except Exception as e:
            hatali += 1
            rapor += f"⚠️ SİSTEM HATASI: {str(e)}\n"
        yield rapor 

    t_sure = int(time.time() - genel_bas)
    yield rapor + f"\n🏁 BİTTİ. Süre: {t_sure//60}dk | Tasarruf: {tasarruf:.2f} GB"

with gr.Blocks(theme=gr.themes.Soft()) as arayuz:
    gr.Markdown("## 📺 Otonom Video İşleyici (Senkronizasyon Korumalı)")
    with gr.Row():
        klasor = gr.Textbox(label="Drive Klasör Yolu", scale=3)
        btn = gr.Button("🚀 Başlat", variant="primary", scale=1)
    with gr.Row():
        durdur = gr.Button("⏸ Duraklat", variant="secondary")
        devam = gr.Button("▶ Devam Et", variant="secondary")
    with gr.Accordion("Ayarlar", open=False):
        cq = gr.Slider(18, 32, value=24, step=1, label="Kalite (CQ)")
        hiz = gr.Dropdown(["slow", "medium", "fast"], value="slow", label="Hız")
        ses = gr.Dropdown(["128k", "192k", "256k"], value="128k", label="Ses (Opus)")
    monitor = gr.Textbox(label="Sistem Monitörü", lines=35, max_lines=100, interactive=False, show_copy_button=True)

    btn.click(fn=toplu_donustur, inputs=[klasor, cq, hiz, ses], outputs=monitor)
    durdur.click(fn=islemi_durdur, outputs=monitor)
    devam.click(fn=islemi_devam_ettir, outputs=monitor)

arayuz.queue().launch(share=True, debug=True)
