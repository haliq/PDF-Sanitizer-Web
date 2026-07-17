# PDF Sanitizer Web — Versi Diperbaiki

Aplikasi web untuk memindai dan membersihkan PDF dari objek interaktif atau konstruksi yang sering menyebabkan penolakan validasi JKN Drive BPJS, seperti `/URI`, JavaScript, OpenAction, Embedded File, Launch Action, dan Remote GoTo.

Versi ini dibuat berdasarkan repositori **krisnadwiki/PDF-Sanitizer**, dengan backend dan antarmuka web yang diperbaiki serta diperketat.

## Perbaikan yang diterapkan

1. **Validasi upload lebih aman**
   - Tidak hanya memeriksa ekstensi `.pdf`, tetapi juga header `%PDF-`.
   - Nama file dibersihkan untuk mencegah karakter/path yang tidak aman.
   - Upload ditulis bertahap, tidak seluruhnya ditampung di RAM.
   - Batas default 50 MB per file dan 100 file per sesi.

2. **API lebih konsisten**
   - Error memakai HTTP status yang sesuai (`400`, `404`, `413`, `415`).
   - DPI dibatasi antara 72–300.
   - File ID yang tidak valid disaring sebelum job dibuat.
   - Path internal server tidak dikirim pada status job.

3. **Scanner diperluas**
   - Mendeteksi URI, JavaScript, OpenAction, Embedded File, Launch, GoToR, SubmitForm, ImportData, dan RichMedia.
   - Menggunakan `pikepdf` bila tersedia.
   - Otomatis memakai fallback PyMuPDF bila `pikepdf` tidak terpasang.

4. **Renderer diperbaiki**
   - Tidak lagi membuat objek dokumen gambar sementara yang berpotensi tidak tertutup.
   - Menulis ke file `.part`, kemudian mengganti file hasil secara atomik.
   - Menolak PDF terenkripsi dan PDF tanpa halaman.
   - Metadata hasil dikosongkan.

5. **Verifikasi hasil**
   - Setiap PDF hasil sanitasi dipindai ulang.
   - File hanya dimasukkan ke ZIP bila hasil verifikasi dinyatakan aman.

6. **Halaman web baru**
   - Responsif untuk desktop dan mobile.
   - Drag-and-drop multi-file.
   - Statistik hasil scan dan pemilihan file.
   - Pilihan DPI 100/150/200/300.
   - Progres real-time dengan WebSocket serta polling sebagai cadangan.
   - Tampilan ungu-putih modern dan mudah dipahami.

## Menjalankan tanpa Docker

### 1. Masuk ke folder aplikasi

```bash
cd PDF-Sanitizer-Web-Fixed
```

### 2. Buat virtual environment

Windows:

```bat
python -m venv venv
venv\Scripts\activate
```

Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instal dependensi

Versi lengkap:

```bash
pip install -r requirements-web.txt
```

Bila instalasi `pikepdf` bermasalah, gunakan versi minimal:

```bash
pip install -r requirements-minimal.txt
```

### 4. Jalankan

```bash
python web_app.py
```

Buka:

```text
http://localhost:8000
```

## Menjalankan dengan Docker

```bash
docker compose up -d --build
```

Buka:

```text
http://localhost:8000
```

Menghentikan aplikasi:

```bash
docker compose down
```

## Konfigurasi environment

| Variabel | Default | Keterangan |
|---|---:|---|
| `PDF_SANITIZER_HOST` | `0.0.0.0` | Host Uvicorn |
| `PDF_SANITIZER_PORT` | `8000` | Port aplikasi |
| `PDF_SANITIZER_DATA_DIR` | folder temporary OS | Penyimpanan sesi dan hasil |
| `PDF_SANITIZER_MAX_FILE_SIZE` | `52428800` | Maksimal byte per file |
| `PDF_SANITIZER_MAX_FILES` | `100` | Maksimal file per sesi |
| `PDF_SANITIZER_WORKERS` | `4` | Worker untuk scan/render |
| `PDF_SANITIZER_SESSION_TTL` | `21600` | Masa simpan sesi dalam detik |

## Pengujian

```bash
python tests/smoke_test.py
```

Pengujian membuat PDF dengan URI, memastikan scanner mendeteksinya, melakukan sanitasi, lalu memastikan PDF hasil sudah aman.

## Catatan penting

Metode sanitasi melakukan **flatten/rasterisasi** seluruh halaman. Tampilan visual dipertahankan, tetapi teks hasil dapat tidak lagi dapat dipilih, disalin, atau dicari. Gunakan DPI 150 sebagai pilihan umum; DPI lebih tinggi menghasilkan file lebih besar dan penggunaan RAM lebih tinggi.
