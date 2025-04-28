# agent/data_manager.py
import logging
import threading
import csv
import os
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class DataManager:
    """Handles thread-safe reading and writing to the leads CSV file."""
    def __init__(self, filename="leads.csv"):
        # ... (keep __init__, _initialize_csv, _read_all, _write_all - unchanged) ...
        self.filename = filename
        self.lock = threading.Lock()
        self.fieldnames = [
            'lead_id', 'name', 'age', 'country', 'interest', 'status',
            'last_agent_msg_ts', 'follow_up_sent_flag'
        ]
        self._initialize_csv()
        logger.info(f"DataManager initialized for file: {self.filename}")

    def _initialize_csv(self):
        with self.lock:
            file_exists = os.path.exists(self.filename)
            is_empty = file_exists and os.path.getsize(self.filename) == 0
            if not file_exists or is_empty:
                try:
                    with open(self.filename, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames)
                        writer.writeheader()
                    logger.info(f"Initialized CSV file: {self.filename}")
                except IOError as e:
                    logger.error(f"Error initializing CSV file {self.filename}: {e}", exc_info=True)

    def _read_all(self) -> List[Dict[str, str]]:
        rows = []
        if not os.path.exists(self.filename): return rows
        try:
            with open(self.filename, 'r', newline='', encoding='utf-8-sig') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    complete_row = {field: row.get(field, '') for field in self.fieldnames}
                    rows.append(complete_row)
        except Exception as e:
             logger.error(f"Error reading CSV file {self.filename}: {e}", exc_info=True)
             return []
        return rows

    def _write_all(self, data: List[Dict[str, Any]]):
        try:
            with open(self.filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames, extrasaction='ignore')
                writer.writeheader()
                sanitized_data = []
                for row_data in data:
                     sanitized_row = {field: str(row_data.get(field, '')) for field in self.fieldnames}
                     sanitized_data.append(sanitized_row)
                writer.writerows(sanitized_data)
        except IOError as e:
            logger.error(f"Error writing to CSV file {self.filename}: {e}", exc_info=True)

    def update_lead(self, lead_data: Dict[str, Any]):
        # ... (keep update_lead method - unchanged) ...
        lead_data_str = {k: str(v) if v is not None else '' for k, v in lead_data.items()}
        with self.lock:
            rows = self._read_all()
            lead_id_to_update = lead_data_str.get('lead_id')
            if not lead_id_to_update:
                 logger.error("CSV Update Error: lead_id missing in data.")
                 return
            updated = False
            update_values = {field: lead_data_str.get(field) for field in self.fieldnames if field in lead_data_str}
            for i, row in enumerate(rows):
                if row.get('lead_id') == str(lead_id_to_update):
                    rows[i] = {**row, **update_values}
                    updated = True
                    break
            if not updated:
                new_row = {field: update_values.get(field, '') for field in self.fieldnames}
                new_row['lead_id'] = str(lead_id_to_update)
                rows.append(new_row)
                logger.info(f"Adding new lead {lead_id_to_update} to CSV.")
            self._write_all(rows)
            logger.debug(f"CSV updated for lead_id: {lead_id_to_update}")

    # --- ADD THIS METHOD BACK ---
    def get_lead(self, lead_id: str) -> Optional[Dict[str, Any]]:
         """Gets a specific lead's data from CSV."""
         # Ensure comparison is string-based
         lead_id_str = str(lead_id)
         with self.lock:
            rows = self._read_all()
            for row in rows:
                if row.get('lead_id') == lead_id_str:
                    # Return the dictionary representing the row
                    return row
            # Return None if lead_id not found
            return None
    # --- END ADDED METHOD ---

    def get_all_active_leads_for_followup(self) -> List[Dict[str, str]]:
         # ... (keep get_all_active_leads_for_followup method - unchanged) ...
         active_leads = []
         terminal_or_completed_statuses = [
             'secured', 'no_response', 'declined_final',
             'completed', 'initiated', 'terminated'
         ]
         with self.lock:
             rows = self._read_all()
             for row in rows:
                 current_status = row.get('status')
                 if current_status and current_status not in terminal_or_completed_statuses:
                     if row.get('last_agent_msg_ts'):
                         active_leads.append({
                             'lead_id': row.get('lead_id', ''),
                             'last_agent_msg_ts': row.get('last_agent_msg_ts'),
                             'follow_up_sent_flag': row.get('follow_up_sent_flag', 'False'),
                             'status': current_status
                         })
                     else: logger.debug(f"Skipping lead {row.get('lead_id')} for follow-up: Missing timestamp (Status: {current_status})")
                 else: logger.debug(f"Skipping lead {row.get('lead_id')} for follow-up: Status '{current_status}' is terminal or completed.")
         logger.debug(f"Found {len(active_leads)} leads potentially needing follow-up: {[l['lead_id'] for l in active_leads]}")
         return active_leads