import os
import sys
import io
import webview
import pypdf
from pathlib import Path
import threading
import traceback

class Api:
    def __init__(self):
        self._window = None
        self._last_dir = str(Path.home())

    def set_window(self, window):
        self._window = window

    def select_file(self):
        """Opens a native file selection dialog for PDF files."""
        if not self._window:
            return {"status": "error", "message": "Window not initialized"}
        
        try:
            file_types = ('PDF Files (*.pdf)', 'All Files (*.*)')
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, 
                directory=self._last_dir,
                file_types=file_types
            )
            if result and len(result) > 0:
                file_path = result[0]
                self._last_dir = str(Path(file_path).parent.resolve())
                return self.load_pdf_metadata(file_path)
            return {"status": "cancelled"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def select_folder(self):
        """Opens a native folder selection dialog."""
        if not self._window:
            return {"status": "error", "message": "Window not initialized"}
        
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=self._last_dir
            )
            if result and len(result) > 0:
                folder_path = result[0]
                self._last_dir = str(Path(folder_path).resolve())
                return {"status": "success", "folder_path": folder_path}
            return {"status": "cancelled"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def load_pdf_metadata(self, file_path):
        """Validates a PDF file and retrieves page count and other metadata."""
        try:
            file_path = file_path.strip('\'"')
            path = Path(file_path)
            if not path.exists():
                return {"status": "error", "message": "File does not exist."}
            
            size_bytes = path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            
            with open(path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                num_pages = len(reader.pages)
                
                # Fetch basic metadata if available
                info = reader.metadata
                title = info.title if info and info.title else path.name
                author = info.author if info and info.author else "Unknown"

            return {
                "status": "success",
                "file_path": str(path.resolve()),
                "filename": path.name,
                "title": title,
                "author": author,
                "pages": num_pages,
                "size_mb": round(size_mb, 2),
                "suggested_output_dir": str(path.parent.resolve())
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to read PDF file: {str(e)}"
            }

    def open_folder(self, folder_path):
        """Opens the specified folder in Windows Explorer."""
        try:
            folder_path = folder_path.strip('\'"')
            path = Path(folder_path)
            if path.exists() and path.is_dir():
                os.startfile(str(path.resolve()))
                return {"status": "success"}
            return {"status": "error", "message": "Folder does not exist."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def split_pdf(self, file_path, output_dir, mode, config_val):
        """
        Starts the PDF splitting process on a background thread so as not to freeze the UI.
        
        Parameters:
        - file_path: Path to source PDF file.
        - output_dir: Path to destination folder.
        - mode: 'range' or 'size'
        - config_val: Configuration string (e.g. "1-2, 3-5" or "every:3") or float (target size in MB).
        """
        # Run splitting in a separate thread
        thread = threading.Thread(
            target=self._run_split, 
            args=(file_path, output_dir, mode, config_val)
        )
        thread.daemon = True
        thread.start()
        return {"status": "started"}

    def _update_progress(self, progress_pct, message, done=False, error=None):
        """Helper to send progress updates to JavaScript."""
        if self._window:
            # Escape strings for safe JS execution
            safe_msg = message.replace("'", "\\'").replace('"', '\\"')
            safe_err = error.replace("'", "\\'").replace('"', '\\"') if error else 'null'
            if error:
                js = f"window.updateProgress(100, '{safe_msg}', true, '{safe_err}');"
            else:
                js = f"window.updateProgress({progress_pct}, '{safe_msg}', {'true' if done else 'false'}, null);"
            self._window.evaluate_js(js)

    def _run_split(self, file_path, output_dir, mode, config_val):
        try:
            file_path = file_path.strip('\'"')
            output_dir = output_dir.strip('\'"')
            src_path = Path(file_path)
            dest_dir = Path(output_dir)
            
            if not src_path.exists():
                self._update_progress(100, "Error: Source file not found.", done=True, error="Source file not found")
                return
            
            if not dest_dir.exists():
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    self._update_progress(100, f"Error: Cannot create output directory.", done=True, error=str(e))
                    return

            self._update_progress(5, "Opening source PDF...")
            
            reader = pypdf.PdfReader(str(src_path))
            total_pages = len(reader.pages)
            base_name = src_path.stem
            
            if total_pages == 0:
                self._update_progress(100, "Error: PDF has 0 pages.", done=True, error="Empty PDF")
                return

            if mode == 'range':
                self._split_by_range(reader, total_pages, base_name, dest_dir, config_val)
            elif mode == 'size':
                self._split_by_size(reader, total_pages, base_name, dest_dir, config_val)
            else:
                self._update_progress(100, "Error: Invalid split mode.", done=True, error="Invalid mode")
                
        except Exception as e:
            err_trace = traceback.format_exc()
            print(err_trace)
            self._update_progress(100, f"Error occurred during splitting: {str(e)}", done=True, error=str(e))

    def _split_by_range(self, reader, total_pages, base_name, dest_dir, range_config):
        ranges = []
        
        # 1. Parse configuration
        if isinstance(range_config, str) and range_config.startswith("every:"):
            try:
                pages_per_split = int(range_config.split(":")[1])
                if pages_per_split <= 0:
                    raise ValueError("Pages per split must be positive")
                
                for start in range(0, total_pages, pages_per_split):
                    end = min(start + pages_per_split - 1, total_pages - 1)
                    ranges.append((start, end))
            except Exception as e:
                self._update_progress(100, "Error: Invalid 'split every' configuration.", done=True, error=str(e))
                return
        else:
            # Custom ranges comma-separated, e.g., "1-3, 4-7, 10"
            try:
                parts = range_config.split(",")
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    
                    if "-" in part:
                        subparts = part.split("-")
                        if len(subparts) != 2:
                            raise ValueError(f"Invalid range format: {part}")
                        
                        start = int(subparts[0].strip()) - 1
                        end = int(subparts[1].strip()) - 1
                        
                        if start < 0 or end < 0 or start >= total_pages or end >= total_pages:
                            raise ValueError(f"Range {part} out of bounds (1 to {total_pages})")
                        if start > end:
                            raise ValueError(f"Range start exceeds end: {part}")
                        
                        ranges.append((start, end))
                    else:
                        page_num = int(part) - 1
                        if page_num < 0 or page_num >= total_pages:
                            raise ValueError(f"Page number {part} out of bounds (1 to {total_pages})")
                        ranges.append((page_num, page_num))
            except Exception as e:
                self._update_progress(100, f"Range parsing error: {str(e)}", done=True, error=str(e))
                return

        if not ranges:
            self._update_progress(100, "Error: No valid ranges specified.", done=True, error="No ranges")
            return

        total_splits = len(ranges)
        self._update_progress(10, f"Starting range split into {total_splits} parts...")
        
        for idx, (start, end) in enumerate(ranges):
            part_num = idx + 1
            progress = 10 + int((idx / total_splits) * 85)
            
            self._update_progress(progress, f"Generating Part {part_num} (pages {start+1} to {end+1})...")
            
            writer = pypdf.PdfWriter()
            for page_idx in range(start, end + 1):
                writer.add_page(reader.pages[page_idx])
                
            out_filename = f"{base_name}_pages_{start+1}_{end+1}.pdf"
            if start == end:
                out_filename = f"{base_name}_page_{start+1}.pdf"
                
            out_path = dest_dir / out_filename
            
            with open(out_path, 'wb') as f_out:
                writer.write(f_out)
                
        self._update_progress(100, f"Successfully split PDF into {total_splits} parts!", done=True)

    def _split_by_size(self, reader, total_pages, base_name, dest_dir, target_size_mb):
        try:
            target_size_bytes = float(target_size_mb) * 1024 * 1024
            if target_size_bytes <= 0:
                raise ValueError("Target size must be positive")
        except Exception as e:
            self._update_progress(100, "Error: Invalid target size value.", done=True, error=str(e))
            return

        self._update_progress(10, f"Starting size split (target: {target_size_mb} MB per file)...")
        
        parts_created = 0
        chunk_pages = []
        
        page_idx = 0
        while page_idx < total_pages:
            chunk_pages.append(page_idx)
            
            # Check size of the current chunk
            writer = pypdf.PdfWriter()
            for p in chunk_pages:
                writer.add_page(reader.pages[p])
            
            temp_io = io.BytesIO()
            writer.write(temp_io)
            current_size = len(temp_io.getvalue())
            
            # Print for debugging
            print(f"Testing chunk with pages {chunk_pages}. Size: {current_size} bytes (target: {target_size_bytes})")
            
            if current_size > target_size_bytes:
                # Adding this page made it exceed the target size
                if len(chunk_pages) == 1:
                    # Single page exceeds target. We must write it anyway.
                    parts_created += 1
                    self._update_progress(
                        10 + int((page_idx / total_pages) * 85),
                        f"Saving Part {parts_created} (page {page_idx+1}) - Exceeds target size limit..."
                    )
                    out_filename = f"{base_name}_part_{parts_created}.pdf"
                    out_path = dest_dir / out_filename
                    with open(out_path, 'wb') as f_out:
                        writer.write(f_out)
                    
                    chunk_pages = []
                    page_idx += 1
                else:
                    # Recreate writer *without* the last page we just tried
                    chunk_pages.pop() # remove page_idx
                    parts_created += 1
                    
                    p_start = chunk_pages[0] + 1
                    p_end = chunk_pages[-1] + 1
                    self._update_progress(
                        10 + int((page_idx / total_pages) * 85),
                        f"Saving Part {parts_created} (pages {p_start} to {p_end})..."
                    )
                    
                    prev_writer = pypdf.PdfWriter()
                    for p in chunk_pages:
                        prev_writer.add_page(reader.pages[p])
                    
                    out_filename = f"{base_name}_part_{parts_created}.pdf"
                    out_path = dest_dir / out_filename
                    with open(out_path, 'wb') as f_out:
                        prev_writer.write(f_out)
                    
                    # Reset chunk with the current page that overflowed
                    chunk_pages = []
                    # Do not advance page_idx, so it gets processed again in the next chunk
            else:
                # Fits within target size, advance to next page
                page_idx += 1

        # Write remaining pages, if any
        if chunk_pages:
            parts_created += 1
            p_start = chunk_pages[0] + 1
            p_end = chunk_pages[-1] + 1
            self._update_progress(
                95,
                f"Saving final Part {parts_created} (pages {p_start} to {p_end})..."
            )
            
            final_writer = pypdf.PdfWriter()
            for p in chunk_pages:
                final_writer.add_page(reader.pages[p])
                
            out_filename = f"{base_name}_part_{parts_created}.pdf"
            out_path = dest_dir / out_filename
            with open(out_path, 'wb') as f_out:
                final_writer.write(f_out)
                
        self._update_progress(100, f"Successfully split PDF into {parts_created} parts!", done=True)

def main():
    api = Api()
    
    # Resolve the directory of index.html
    # In pyinstaller bundles or normal running, we locate the 'ui' folder
    current_dir = Path(__file__).parent.resolve()
    ui_dir = current_dir / "ui"
    index_html_path = ui_dir / "index.html"
    
    if not index_html_path.exists():
        print(f"Error: UI directory or index.html not found at {index_html_path}")
        sys.exit(1)
        
    # Start webview window
    # We load index.html from disk and expose the Api instance to JavaScript as window.pywebview.api
    window = webview.create_window(
        "Modern PDF Splitter",
        str(index_html_path),
        js_api=api,
        width=850,
        height=680,
        min_size=(750, 600),
        resizable=True,
        background_color="#181824"
    )
    
    api.set_window(window)
    webview.start(debug=False)

if __name__ == "__main__":
    main()
