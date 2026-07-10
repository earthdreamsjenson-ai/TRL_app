import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
from datetime import datetime, timedelta
import io
from collections import Counter  # 【追加】残試合数カウント用

st.set_page_config(page_title="TRL日程管理システム", layout="wide")
st.title("⚾ TRL日程管理システム")

# ==========================================
# 1. Googleスプレッドシートからの全7シート読込
# ==========================================
conn = st.connection("gsheets", type=GSheetsConnection)

def load_all_data():
    pool_df = conn.read(worksheet="match_pool", ttl=600)
    sched_df = conn.read(worksheet="schedule", ttl=600)
    res_df = conn.read(worksheet="results", ttl=600)
    teams_df = conn.read(worksheet="teams", ttl=600)
    grounds_df = conn.read(worksheet="grounds", ttl=600)
    ng_df = conn.read(worksheet="unavailable_days", ttl=600)
    slots_df = conn.read(worksheet="available_slots", ttl=600)
    return pool_df, sched_df, res_df, teams_df, grounds_df, ng_df, slots_df

pool_df, sched_df, res_df, teams_df, grounds_df, ng_df, slots_df = load_all_data()

# マスタデータの辞書化・リスト化
all_teams = teams_df['team'].tolist()
team_allow_far = dict(zip(teams_df['team'], teams_df['allow_far']))
ground_options = grounds_df['name'].tolist()
ground_is_far = dict(zip(grounds_df['name'], grounds_df['is_far']))
ground_maps = dict(zip(grounds_df['name'], grounds_df['maps_url']))

# ==========================================
# 2. 日程自動作成ロジック（バックトラッキング）
# ==========================================
def make_monthly_schedule(match_list, slots, ng_days_dict, team_far_dict):
    allocated_matches = []
    team_monthly_counts = {team: 0 for team in all_teams}

    def get_teams_playing_on(date):
        playing = set()
        for am in allocated_matches:
            if am['date'] == date and am['match'] is not None:
                playing.add(am['match'][0])
                playing.add(am['match'][1])
        return playing

    def backtrack(slot_idx):
        if slot_idx == len(slots):
            return True
            
        current_slot = slots[slot_idx]
        date = current_slot['date']
        is_far_ground = current_slot['is_far']
        teams_playing_today = get_teams_playing_on(date)
        
        for i, match in enumerate(match_list):
            t1, t2 = match
            
            # 各種制約チェック
            if team_monthly_counts[t1] >= 2 or team_monthly_counts[t2] >= 2:
                continue
            if t1 in teams_playing_today or t2 in teams_playing_today:
                continue
            if ng_days_dict.get(t1) == date or ng_days_dict.get(t2) == date:
                continue
            if is_far_ground and not (team_far_dict.get(t1, False) and team_far_dict.get(t2, False)):
                continue
                
            allocated_matches.append({
                'id': current_slot['id'], # グラウンド枠のIDを引き継ぐ
                'date': date,
                'slot': current_slot['slot'],
                'ground_name': current_slot['ground_name'],
                'match': match
            })
            team_monthly_counts[t1] += 1
            team_monthly_counts[t2] += 1
            match_list.pop(i)
            
            if backtrack(slot_idx + 1):
                return True
                
            match_list.insert(i, match)
            team_monthly_counts[t1] -= 1
            team_monthly_counts[t2] -= 1
            allocated_matches.pop()
            
        # 試合が組めなかった場合は「空き枠」として処理
        allocated_matches.append({
            'id': current_slot['id'],
            'date': date,
            'slot': current_slot['slot'],
            'ground_name': current_slot['ground_name'],
            'match': None
        })
        if backtrack(slot_idx + 1):
            return True
        allocated_matches.pop()
        return False

    # 【改善機能】プール内の残試合数が多いチームの対戦を最優先にするロジック
    # 1. 各チームが現在のプール内に何回登場するか（＝残試合数）をカウント
    pool_team_counts = Counter([team for match in match_list for team in match])
    
    # 2. 最初に対戦リストをシャッフル（残試合数が同じチーム同士のタイブレークにおける偏りを防ぐ）
    random.shuffle(match_list)
    
    # 3. 対戦ペアの「双方の残試合数の合計」が多い順（降順）にソート
    # 例: 残り5試合のチームA vs 残り4試合のチームB (合計9) を、残り2試合 vs 残り1試合 (合計3) より優先する
    match_list.sort(key=lambda m: pool_team_counts[m[0]] + pool_team_counts[m[1]], reverse=True)

    if backtrack(0):
        new_games = []
        filled_slot_ids = []
        for m in allocated_matches:
            if m['match'] is not None:
                new_games.append({
                    'id': m['id'],
                    'date': m['date'],
                    'slot': m['slot'],
                    'ground_name': m['ground_name'],
                    'team1': m['match'][0],
                    'team2': m['match'][1]
                })
                filled_slot_ids.append(m['id']) # マッチングが成功した枠のID
        return pd.DataFrame(new_games), match_list, filled_slot_ids
    return pd.DataFrame(), match_list, []

# ==========================================
# 3. 画面UIレイアウト（5つのタブ）
# ==========================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 NG日登録", 
    "🛠️ グラウンド枠登録・日程作成", 
    "🏆 試合結果入力", 
    "⚙️ マスタ・データ確認",
    "📊 残試合数確認"
])

# --- タブ1: NG日登録 ---
with tab1:
    st.header("📢 NG日登録")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        select_team = st.selectbox("あなたのチーム名を選択してください", ["選択してください"] + all_teams)
    with col_t2:
        # 【改善機能】実行月を基準に、自動で「翌月の日曜日」だけを抽出して選択肢にするロジック
        today = datetime.now()
        
        # 1. 次月の1日を算出
        if today.month == 12:
            next_month_first = datetime(today.year + 1, 1, 1)
        else:
            next_month_first = datetime(today.year, today.month + 1, 1)
            
        # 2. 次月の翌月の1日を算出（ループ終了条件）
        if next_month_first.month == 12:
            following_month_first = datetime(next_month_first.year + 1, 1, 1)
        else:
            following_month_first = datetime(next_month_first.year, next_month_first.month + 1, 1)
            
        # 3. 次月の日曜日（weekday == 6）をリストアップ
        next_month_sundays = []
        curr = next_month_first
        while curr < following_month_first:
            if curr.weekday() == 6:  # 6は日曜日
                next_month_sundays.append(curr.date())
            curr += timedelta(days=1)
            
        # 4. st.date_input から st.selectbox へ変更
        select_ng_date = st.selectbox(
            "試合NGにする日を選択（次月の日曜日のみ）", 
            options=next_month_sundays,
            format_func=lambda x: x.strftime('%Y-%m-%d（日）')
        )
        
        target_month = str(select_ng_date)[:7]
        st.caption(f"対象月（自動取得）: **{target_month}**")
        
    if select_team != "選択してください":
        if st.button("💾 NG日を登録・上書きする", type="primary"):
            new_ng_row = pd.DataFrame([{"team": select_team, "year_month": target_month, "ng_date": str(select_ng_date)}])
            filtered_ng = ng_df[~((ng_df['team'] == select_team) & (ng_df['year_month'] == target_month))]
            updated_ng_df = pd.concat([filtered_ng, new_ng_row], ignore_index=True)
            conn.update(worksheet="unavailable_days", data=updated_ng_df)
            st.cache_data.clear()
            st.success(f"【{select_team}】の {target_month} のNG日を保存（上書き）しました！")
            st.rerun()

    st.subheader("👀 現在の各チームのNG日登録状況")
    st.dataframe(ng_df, use_container_width=True)

# --- タブ2: グラウンド枠登録 ＆ 日程自動作成 ---
with tab2:
    st.header("🛠️ グラウンド枠登録・日程作成")
    
    # 2-1. グラウンド枠の一時保存フォーム
    st.subheader("① 確保したグラウンド枠の登録（随時保存可能）")
    
    reg_mode = st.radio(
        "登録モードを選択してください", 
        ["1件ずつ登録", "画面でまとめて登録 (Excel風)", "テキストを直接貼り付けて登録", "CSVファイルから一括アップロード"], 
        horizontal=True
    )
    
    # --- モード1: 1件ずつ登録 ---
    if reg_mode == "1件ずつ登録":
        with st.form("slot_input_form"):
            col_g1, col_g2, col_g3 = st.columns(3)
            slot_date = col_g1.date_input("確保した日付")
            slot_time = col_g2.selectbox("時間枠", [
                "9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"
            ])
            slot_ground = col_g3.selectbox("グラウンド名", ground_options)
            
            submit_slot = st.form_submit_button("💾 この枠をデータベースに保存する")
            if submit_slot:
                generated_id = f"S_{slot_date}_{slot_time}_{slot_ground}"
                ym = str(slot_date)[:7]
                
                new_slot_row = pd.DataFrame([{
                    "id": generated_id, "date": str(slot_date), "slot": slot_time,
                    "ground_name": slot_ground, "year_month": ym, "status": "未割り当て"
                }])
                filtered_slots = slots_df[slots_df['id'] != generated_id]
                updated_slots_df = pd.concat([filtered_slots, new_slot_row], ignore_index=True)
                
                conn.update(worksheet="available_slots", data=updated_slots_df)
                st.cache_data.clear()
                st.success(f"枠 {generated_id} を「未割り当て」として保存しました！")
                st.rerun()

    # --- モード2: 画面でまとめて登録（Excel風） ---
    elif reg_mode == "画面でまとめて登録 (Excel風)":
        st.markdown("💡 **Excel等から複数行をコピー（Ctrl+C）し、下の表に貼り付け（Ctrl+V）が可能です。**")
        input_template = pd.DataFrame(columns=["date", "slot", "ground_name"])
        edited_df = st.data_editor(
            input_template, 
            num_rows="dynamic", 
            use_container_width=True,
            column_config={
                "date": st.column_config.DateColumn("確保した日付", required=True),
                "slot": st.column_config.SelectboxColumn("時間枠", options=[
                    "9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"
                ], required=True),
                "ground_name": st.column_config.SelectboxColumn("グラウンド名", options=ground_options, required=True)
            }
        )
        
        if st.button("💾 入力した枠をまとめてデータベースに保存する", type="primary", key="bulk_save_editor"):
            valid_df = edited_df.dropna(subset=["date", "slot", "ground_name"]).copy()
            if valid_df.empty:
                st.warning("登録する有効なデータが入力されていません。")
            else:
                valid_df['date'] = valid_df['date'].astype(str)
                valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                valid_df['year_month'] = valid_df['date'].str[:7]
                valid_df['status'] = "未割り当て"
                
                new_ids = valid_df['id'].tolist()
                filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                
                conn.update(worksheet="available_slots", data=updated_slots_df)
                st.cache_data.clear()
                st.success(f"🎉 {len(valid_df)} 件のグラウンド枠をまとめて保存しました！")
                st.rerun()

    # --- モード3: テキストを直接貼り付けて登録 ---
    elif reg_mode == "テキストを直接貼り付けて登録":
        st.markdown("💡 **カンマ区切りのテキストをそのまま貼り付けて一括登録できます。**")
        default_example = "日,時間,施設名\n2026/07/05,12:00-15:00,額田G\n2026/07/05,15:00-18:00,額田G\n2026/07/12,12:00-15:00,三百田公園G"
        
        bulk_text = st.text_area(
            "ここにデータを貼り付けてください（1行目は「日,時間,施設名」にしてください）",
            value=default_example,
            height=200
        )
        
        if st.button("🚀 貼り付けたテキストから一括登録を実行", type="primary", key="bulk_save_text"):
            if not bulk_text.strip() or bulk_text.strip() == "日,時間,施設名":
                st.warning("登録するデータが入力されていません。")
            else:
                try:
                    parsed_df = pd.read_csv(io.StringIO(bulk_text.strip()))
                    expected_cols = ["日", "時間", "施設名"]
                    if not all(col in parsed_df.columns for col in expected_cols):
                        st.error("❌ 1行目のヘッダー（列名）は必ず「日,時間,施設名」にしてください。")
                    else:
                        parsed_df = parsed_df.rename(columns={"日": "date", "時間": "slot", "施設名": "ground_name"})
                        valid_df = parsed_df.dropna(subset=["date", "slot", "ground_name"]).copy()
                        
                        # 日付フォーマットの自動変換 (YYYY/MM/DD -> YYYY-MM-DD)
                        valid_df['date'] = pd.to_datetime(valid_df['date']).dt.strftime('%Y-%m-%d')
                        
                        valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                        valid_df['year_month'] = valid_df['date'].str[:7]
                        valid_df['status'] = "未割り当て"
                        
                        # マスタチェック
                        invalid_grounds = valid_df[~valid_df['ground_name'].isin(ground_options)]['ground_name'].unique()
                        if len(invalid_grounds) > 0:
                            st.warning(f"⚠️ 注意: 「{', '.join(invalid_grounds)}」はグラウンドマスタに登録されていない名称です。自動作成に影響する可能性があるためご確認ください。")
                        
                        new_ids = valid_df['id'].tolist()
                        filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                        updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                        
                        conn.update(worksheet="available_slots", data=updated_slots_df)
                        st.cache_data.clear()
                        st.success(f"🎉 テキストから {len(valid_df)} 件のグラウンド枠を正常に登録しました！")
                        st.rerun()
                except Exception as e:
                    st.error(f"🚨 テキストの解析中にエラーが発生しました。エラー詳細: {e}")

    # --- モード4: CSVファイルから一括登録 ---
    elif reg_mode == "CSVファイルから一括アップロード":
        st.markdown("💡 **以下のヘッダー（列名）を持つCSVファイルをアップロードしてください。**")
        st.code("date,slot,ground_name\n2026-07-12,13:00-17:00,南明柄グラウンド")
        
        uploaded_file = st.file_uploader("CSVファイルを選択", type=["csv"])
        if uploaded_file is not None:
            try:
                uploaded_df = pd.read_csv(uploaded_file)
                required_cols = ["date", "slot", "ground_name"]
                
                if not all(col in uploaded_df.columns for col in required_cols):
                    st.error(f"❌ CSVファイルのヘッダーが正しくありません。 {required_cols} を含めてください。")
                else:
                    st.write("📋 アップロードデータのプレビュー:")
                    st.dataframe(uploaded_df, use_container_width=True)
                    
                    if st.button("💾 CSVのデータをデータベースに保存する", type="primary", key="bulk_save_csv"):
                        valid_df = uploaded_df.dropna(subset=["date", "slot", "ground_name"]).copy()
                        valid_df['date'] = valid_df['date'].astype(str)
                        valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                        valid_df['year_month'] = valid_df['date'].str[:7]
                        valid_df['status'] = "未割り当て"
                        
                        new_ids = valid_df['id'].tolist()
                        filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                        updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                        
                        conn.update(worksheet="available_slots", data=updated_slots_df)
                        st.cache_data.clear()
                        st.success(f"🎉 CSVから {len(valid_df)} 件のグラウンド枠をまとめて保存しました！")
                        st.rerun()
            except Exception as e:
                st.error(f"🚨 ファイルの読み込み中にエラーが発生しました: {e}")

    st.markdown("---")
    
    # 2-2. 保存された枠を使った日程の自動作成
    st.subheader("② 次月の日程自動作成の実行")
    target_month_sched = st.selectbox("作成する対象月を選択", ["2026-07", "2026-08", "2026-09"], index=1, key="sb_month")
    
    current_month_slots = slots_df[(slots_df['year_month'] == target_month_sched) & (slots_df['status'] == "未割り当て")].copy()
    
    st.write(f"📊 現在スプレッドシートに保存されている **{target_month_sched} 分の未割り当て枠**: {len(current_month_slots)} 件")
    st.dataframe(current_month_slots, use_container_width=True)
    
    if st.button("🔥 保存された枠を元に日程を自動生成する", type="primary"):
        if current_month_slots.empty:
            st.error("自動生成に使用できる「未割り当て」のグラウンド枠がありません。先に上のフォームから枠を登録・保存してください。")
        else:
            current_month_slots['is_far'] = current_month_slots['ground_name'].map(ground_is_far)
            slots_input_list = current_month_slots.to_dict('records')
            
            current_pool = list(zip(pool_df['team1'], pool_df['team2']))
            monthly_ng_df = ng_df[ng_df['year_month'] == target_month_sched]
            ng_days_dict = dict(zip(monthly_ng_df['team'], monthly_ng_df['ng_date']))
            
            new_sched_df, rem_pool_list, filled_slot_ids = make_monthly_schedule(
                current_pool, slots_input_list, ng_days_dict, team_allow_far
            )
            
            if not new_sched_df.empty:
                updated_sched_df = pd.concat([sched_df, new_sched_df], ignore_index=True)
                conn.update(worksheet="schedule", data=updated_sched_df)
                
                updated_pool_df = pd.DataFrame(rem_pool_list, columns=['team1', 'team2'])
                conn.update(worksheet="match_pool", data=updated_pool_df)
                
                slots_df.loc[slots_df['id'].isin(filled_slot_ids), 'status'] = '割り当て済み'
                conn.update(worksheet="available_slots", data=slots_df)
                st.cache_data.clear()
                
                st.success("🎉 日程の自動生成およびグラウンド枠のステータス更新が完了しました！")
                st.rerun()
            else:
                st.error("❌ 条件（各チームのNG日や遠方制限など）が厳しく、マッチングする組み合わせが見つかりませんでした。枠を増やすか調整してください。")

    if not sched_df.empty:
        st.subheader("🗓️ 確定スケジュール一覧")
        display_sched = sched_df.copy()
        display_sched['GoogleMap_URL'] = display_sched['ground_name'].map(ground_maps)
        st.dataframe(display_sched, use_container_width=True)

# ==========================================
# 4. 試合結果入力（タブ3）
# ==========================================
with tab3:
    st.header("🏆 試合結果入力")
    if sched_df.empty:
        st.warning("確定した日程がありません。")
    else:
        for idx, row in sched_df.iterrows():
            m_id = row['id']
            existing_res = res_df[res_df['id'] == m_id]
            current_status = existing_res['status'].values[0] if not existing_res.empty else "未消化"
            current_score = existing_res['score'].values[0] if not existing_res.empty else "-"
            
            status_display_text = f"現在のステータス: {current_status}"
            
            if current_status == "通常消化":
                status_emoji = "🟢"
                bg_color = "#e6f4ea"
                text_color = "#137333"
                try:
                    if "-" in str(current_score):
                        s1_str, s2_str = str(current_score).split("-")
                        s1, s2 = int(s1_str), int(s2_str)
                        if s1 > s2:
                            status_display_text += f" (⭕ 勝者: {row['team1']} [{s1}] / ❌ 敗者: {row['team2']} [{s2}])"
                        elif s2 > s1:
                            status_display_text += f" (❌ 敗者: {row['team1']} [{s1}] / ⭕ 勝者: {row['team2']} [{s2}])"
                        else:
                            status_display_text += f" (🔺 引き分け: {row['team1']} [{s1}] - [{s2}] {row['team2']})"
                    else:
                        status_display_text += f" (スコア: {current_score})"
                except Exception:
                    status_display_text += f" (スコア: {current_score})"
                
            elif current_status == "雨天中止":
                status_emoji = "⚫"
                bg_color = "#f1f3f4"
                text_color = "#5f6368"
                
            elif current_status == "不戦敗":
                status_emoji = "🟡"
                bg_color = "#fef7e0"
                text_color = "#b06000"
                if current_score == "0-10":
                    status_display_text += f" (❌ 不戦敗: {row['team1']} / ⭕ 不戦勝: {row['team2']})"
                elif current_score == "10-0":
                    status_display_text += f" (⭕ 不戦勝: {row['team1']} / ❌ 不戦敗: {row['team2']})"
                else:
                    status_display_text += f" (スコア: {current_score})"
            else:
                status_emoji = "⚪"
                bg_color = "#e8f0fe"
                text_color = "#1a73e8"
            
            expander_title = f"{status_emoji} 【{row['date']} {row['slot']} @{row['ground_name']}】 {row['team1']} vs {row['team2']} (現在の状態: {current_status})"
            
            with st.expander(expander_title):
                status_bar_html = f"""
                <div style="background-color: {bg_color}; color: {text_color}; padding: 10px 12px; border-radius: 5px; margin-bottom: 15px; font-weight: bold; border-left: 6px solid {text_color};">
                    {status_emoji} {status_display_text}
                </div>
                """
                st.markdown(status_bar_html, unsafe_allow_html=True)
                
                status = st.selectbox("試合ステータスを更新する", ["未消化", "通常消化", "雨天中止", "不戦敗"], key=f"st_{m_id}")
                
                if status == "未消化":
                    if st.button("結果を保存", key=f"save_unplayed_{m_id}"):
                        if current_status in ["通常消化", "不戦敗"]:
                            updated_res_df = res_df[res_df['id'] != m_id]
                        else:
                            new_res = pd.DataFrame([{"id": m_id, "status": "未消化", "score": "-"}])
                            updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("ステータスを『未消化』に更新しました。")
                        st.rerun()

                elif status == "通常消化":
                    sc1 = st.number_input(f"{row['team1']} スコア", min_value=0, value=0, key=f"sc1_{m_id}")
                    sc2 = st.number_input(f"{row['team2']} スコア", min_value=0, value=0, key=f"sc2_{m_id}")
                    if st.button("結果を保存", key=f"save_{m_id}"):
                        new_res = pd.DataFrame([{"id": m_id, "status": "通常消化", "score": f"{sc1}-{sc2}"}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("試合結果を保存しました。")
                        st.rerun()
                        
                elif status == "雨天中止":
                    if st.button("🚨 雨天中止を確定して再試合プールへ戻す", key=f"rain_{m_id}"):
                        updated_sched_df = sched_df[sched_df['id'] != m_id]
                        conn.update(worksheet="schedule", data=updated_sched_df)
                        
                        canceled_match = pd.DataFrame([{"team1": row['team1'], "team2": row['team2']}])
                        updated_pool_df = pd.concat([canceled_match, pool_df], ignore_index=True)
                        conn.update(worksheet="match_pool", data=updated_pool_df)
                        
                        slots_df.loc[slots_df['id'] == m_id, 'status'] = '未割り当て'
                        conn.update(worksheet="available_slots", data=slots_df)
                        
                        if current_status in ["通常消化", "不戦敗"]:
                            updated_res_df = res_df[res_df['id'] != m_id]
                        else:
                            new_res = pd.DataFrame([{"id": m_id, "status": "雨天中止", "score": "-"}])
                            updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("スケジュールを削除し、対戦カードをプールに戻しました。")
                        st.rerun()

                elif status == "不戦敗":
                    lose_team = st.radio(
                        "どちらのチームが不戦敗（負け）となりましたか？", 
                        [row['team1'], row['team2']], 
                        key=f"lose_{m_id}"
                    )
                    
                    if st.button("結果を保存", key=f"save_forfeit_{m_id}"):
                        if lose_team == row['team1']:
                            final_score = "0-10"
                        else:
                            final_score = "10-0"
                            
                        new_res = pd.DataFrame([{"id": m_id, "status": "不戦敗", "score": final_score}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success(f"不戦敗の結果を保存しました。（スコア: {final_score}）")
                        st.rerun()

# --- タブ4: 各種マスタデータの確認 ---
with tab4:
    st.header("⚙️ マスタ・データ確認")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🏟️ グラウンドマスタ")
        st.dataframe(grounds_df)
        st.subheader("📋 確保グラウンド枠の全履歴 (`available_slots`)")
        st.dataframe(slots_df)
    with c2:
        st.subheader("🏃 チームマスタ")
        st.dataframe(teams_df)
        st.subheader("🔥 残りの未消化試合プール")
        st.dataframe(pool_df)

# --- タブ5: 各チームの残試合数確認【改良版】 ---
with tab5:
    st.header("📊 各チームの残試合数確認")
    st.markdown("リーグ全体の残り試合数の集計状況です。総残試合数が多い順に表示しています。")

    # 本日の日付を YYYY-MM-DD 形式の文字列で取得
    today_str = datetime.now().strftime('%Y-%m-%d')

    remaining_data = []
    
    # 【修正箇所】処理が完了した試合（通常消化、不戦敗、雨天中止）のIDを文字列型で一括抽出
    if not res_df.empty and 'id' in res_df.columns:
        exclude_ids = set(res_df[res_df['status'].isin(['通常消化', '不戦敗', '雨天中止'])]['id'].astype(str).tolist())
    else:
        exclude_ids = set()

    for team in all_teams:
        # 1. 未日程の試合数 (match_pool に残っている対戦)
        unallocated = ((pool_df['team1'] == team) | (pool_df['team2'] == team)).sum() if not pool_df.empty else 0
        
        # 2. 日程確定済みの未消化試合（過去 / 未来に分解）
        past_unplayed = 0
        future_unplayed = 0
        
        if not sched_df.empty:
            # IDを強制的に文字列化して、処理済みID（消化済＋雨天中止）を除外
            unplayed_sched = sched_df[~sched_df['id'].astype(str).isin(exclude_ids)]
            
            # 自チームが関わる試合に絞り込み
            team_sched = unplayed_sched[(unplayed_sched['team1'] == team) | (unplayed_sched['team2'] == team)]
            
            if not team_sched.empty:
                # 試合日が今日より前のもの（結果の入力忘れ・漏れ）
                past_unplayed = (team_sched['date'].astype(str) < today_str).sum()
                # 試合日が今日以降のもの（これから開催予定の未来の試合）
                future_unplayed = (team_sched['date'].astype(str) >= today_str).sum()
        
        remaining_data.append({
            "チーム名": team,
            "総残試合数": unallocated + past_unplayed + future_unplayed,
            "未日程 (プール内)": unallocated,
            "日程済 (過去の未消化)": past_unplayed,
            "日程済 (未来の未消化)": future_unplayed
        })

    # データフレームに変換し表示
    remaining_df = pd.DataFrame(remaining_data).sort_values(by="総残試合数", ascending=False)
    st.dataframe(remaining_df, use_container_width=True, hide_index=True)
    
    st.info(f"💡 **「日程済 (過去の未消化)」**は、試合日が既に過ぎている（{today_str} より前）のに結果が入力されていない試合です。結果の入力漏れがないかご確認ください。\n\n"
            f"💡 **「日程済 (未来の未消化)」**は、これから行う予定の試合（自動生成したばかりの次月以降の日程など）です。")