import streamlit as st
from streamlit.connections import BaseConnection
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from gspread_dataframe import set_with_dataframe
import re
import csv
import io

class GSheetsConnection(BaseConnection[gspread.Client]):
    def _connect(self, **kwargs) -> gspread.Client:
        # Resolve secrets
        secrets_dict = {}
        if hasattr(self, "_secrets") and self._secrets:
            secrets_dict = dict(self._secrets)
        elif "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
            secrets_dict = dict(st.secrets["connections"]["gsheets"])
        
        # Check for service account credentials
        if "private_key" in secrets_dict or "private_key_id" in secrets_dict:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            # Replace escaped newlines in private key
            if "private_key" in secrets_dict:
                secrets_dict["private_key"] = secrets_dict["private_key"].replace("\\n", "\n")
            
            # Extract only service account fields
            sa_keys = [
                "type", "project_id", "private_key_id", "private_key",
                "client_email", "client_id", "auth_uri", "token_uri",
                "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain"
            ]
            sa_info = {k: secrets_dict[k] for k in sa_keys if k in secrets_dict}
            
            try:
                creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
                return gspread.authorize(creds)
            except Exception as e:
                st.error(f"gspreadのサービスアカウント認証に失敗しました: {e}")
                return None
        return None

    def read(self, worksheet: str = None, spreadsheet: str = None, ttl: int = None, **kwargs) -> pd.DataFrame:
        # Determine the spreadsheet URL
        url = spreadsheet
        if not url:
            secrets_dict = {}
            if hasattr(self, "_secrets") and self._secrets:
                secrets_dict = dict(self._secrets)
            elif "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
                secrets_dict = dict(st.secrets["connections"]["gsheets"])
            url = secrets_dict.get("spreadsheet")

        if not url:
            raise ValueError("スプレッドシートのURLが指定されていません。secrets.tomlに登録するか、引数で渡してください。")

        # Extract spreadsheet ID from URL
        spreadsheet_id = None
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
        if match:
            spreadsheet_id = match.group(1)

        # 1. gspreadでの読み込み（認証情報がある場合）
        client = self._instance
        if client:
            try:
                sh = client.open_by_url(url)
                ws = sh.worksheet(worksheet)
                data = ws.get_all_values()
                if not data:
                    return pd.DataFrame()
                
                # StringIOとpd.read_csvを利用して、型変換と空白行除去を標準のread_csvと統一
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerows(data)
                output.seek(0)
                return pd.read_csv(output)
            except Exception as e:
                # 読み込みに失敗した場合はパブリック経由でのフォールバックを試みる
                pass

        # 2. パブリックURL経由での読み込み（認証情報がない場合）
        if spreadsheet_id:
            try:
                csv_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={worksheet}"
                return pd.read_csv(csv_url)
            except Exception as e:
                raise RuntimeError(f"パブリックURLからシート '{worksheet}' の読み込みに失敗しました: {e}")
        else:
            raise ValueError(f"スプレッドシートURLからIDをパースできませんでした: {url}")

    def update(self, worksheet: str = None, spreadsheet: str = None, data: pd.DataFrame = None, **kwargs):
        # Determine the spreadsheet URL
        url = spreadsheet
        if not url:
            secrets_dict = {}
            if hasattr(self, "_secrets") and self._secrets:
                secrets_dict = dict(self._secrets)
            elif "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
                secrets_dict = dict(st.secrets["connections"]["gsheets"])
            url = secrets_dict.get("spreadsheet")

        if not url:
            raise ValueError("スプレッドシートのURLが指定されていません。")

        client = self._instance
        if not client:
            raise RuntimeError("gspreadが認証されていません。secretsのサービスアカウント情報を確認してください。")

        # Open spreadsheet and worksheet
        sh = client.open_by_url(url)
        try:
            ws = sh.worksheet(worksheet)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=worksheet, rows="100", cols="20")

        # Clear existing sheet content
        ws.clear()

        # Write data
        set_with_dataframe(ws, data, include_index=False, resize=True)
