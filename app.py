import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
from datetime import datetime, timedelta
import io
from collections import Counter

st.set_page_config(page_title="TRL日程管理", layout="wide")
st.title("⚾ TRL日程管理")

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

# 【重要】読み込み直後に型の不一致を解消
if 'allow_far' in teams_df.columns:
    teams_df['allow_far'] = teams_df['allow_far'].fillna(False).astype(bool)
if 'is_far' in grounds_df.columns:
    grounds_df['is_far'] = grounds_df['is_far'].fillna(False).astype(bool)
if 'maps_url' in grounds_df.columns:
    grounds_df['maps_url'] = grounds_df['maps_url'].fillna("").astype(str)

# マスタデータの辞書化・リスト化
all_teams = teams_df['team'].tolist()
team_allow_far = dict(zip(teams_df['team'], teams_df['allow_far']))
ground_options = grounds_df['name'].tolist()
ground_is_far = dict(zip(grounds_df['name'], grounds_df['is_far']))
ground_maps = dict(zip(grounds_df['name'], grounds_df['maps_url']))

# ==========================================
# 2. 日程自動作成ロジック
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
                'id': current_slot['id'], 
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

    pool_team_counts = Counter([team for match in match_list for team in match])
    random.shuffle(match_list)
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
                filled_slot_ids.append(m['id'])
        return pd.DataFrame(new_games), match_list, filled_slot_ids
    return pd.DataFrame(), match_list, []

# ==========================================
# 3. 画面UIレイアウト
# ==========================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 NG日登録", 
    "🛠️ グラウンド枠・日程作成", 
    "🏆 試合結果入力", 
    "📊 残試合数確認",
    "⚙️ マスタメンテ・データ確認"
])

# --- タブ1: NG日登録 ---
with tab1:
    st.header("📢 NG日登録")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        select_team = st.selectbox("チーム名", ["選択してください"] + all_teams)
    with col_t2:
        today = datetime.now()
        if today.month == 12: next_month_first = datetime(today.year + 1, 1, 1)
        else: next_month_first = datetime(today.year, today.month + 1, 1)
        if next_month_first.month == 12: following_month_first = datetime(next_month_first.year + 1, 1, 1)
        else: following_month_first = datetime(next_month_first.year, next_month_first.month + 1, 1)
        
        next_month_sundays = []
        curr = next_month_first
        while curr < following_month_first:
            if curr.weekday() == 6: next_month_sundays.append(curr.date())
            curr += timedelta(days=1)
            
        select_ng_date = st.selectbox(
            "次月NG日", 
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

    st.subheader("👀 NG日登録状況")
    st.dataframe(ng_df, use_container_width=True)

# --- タブ2: グラウンド枠登録・日程作成 ---
with tab2:
    st.header("🛠️ グラウンド枠・日程作成")
    
    st.subheader("① グラウンド枠の登録")
    reg_mode = st.radio(
        "登録モードを選択してください", 
        ["1件ずつ登録", "画面でまとめて登録 (Excel風)", "テキストを直接貼り付けて登録", "CSVファイルから一括アップロード"], 
        horizontal=True
    )
    
    if reg_mode == "1件ずつ登録":
        with st.form("slot_input_form"):
            col_g1, col_g2, col_g3 = st.columns(3)
            slot_date = col_g1.date_input("確保した日付")
            slot_time = col_g2.selectbox("時間枠", ["9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"])
            slot_ground = col_g3.selectbox("グラウンド名", ground_options)
            
            submit_slot = st.form_submit_button("💾 この枠をデータベースに保存する")
            if submit_slot:
                generated_id = f"S_{slot_date}_{slot_time}_{slot_ground}"
                ym = str(slot_date)[:7]
                new_slot_row = pd.DataFrame([{"id": generated_id, "date": str(slot_date), "slot": slot_time, "ground_name": slot_ground, "year_month": ym, "status": "未割り当て"}])
                filtered_slots = slots_df[slots_df['id'] != generated_id]
                updated_slots_df = pd.concat([filtered_slots, new_slot_row], ignore_index=True)
                conn.update(worksheet="available_slots", data=updated_slots_df)
                st.cache_data.clear()
                st.rerun()

    elif reg_mode == "画面でまとめて登録 (Excel風)":
        input_template = pd.DataFrame(columns=["date", "slot", "ground_name"])
        edited_df = st.data_editor(
            input_template, num_rows="dynamic", use_container_width=True,
            column_config={
                "date": st.column_config.DateColumn("確保した日付", required=True),
                "slot": st.column_config.SelectboxColumn("時間枠", options=["9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"], required=True),
                "ground_name": st.column_config.SelectboxColumn("グラウンド名", options=ground_options, required=True)
            }
        )
        if st.button("💾 保存", type="primary", key="bulk_save_editor"):
            valid_df = edited_df.dropna(subset=["date", "slot", "ground_name"]).copy()
            valid_df['date'] = valid_df['date'].astype(str)
            valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
            valid_df['year_month'] = valid_df['date'].str[:7]
            valid_df['status'] = "未割り当て"
            new_ids = valid_df['id'].tolist()
            filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
            conn.update(worksheet="available_slots", data=pd.concat([filtered_slots, valid_df], ignore_index=True))
            st.cache_data.clear()
            st.rerun()

    elif reg_mode == "テキストを直接貼り付けて登録":
        bulk_text = st.text_area("カンマ区切りのテキスト（日,時間,施設名）を貼り付け", height=200)
        if st.button("🚀 登録", type="primary"):
            parsed_df = pd.read_csv(io.StringIO(bulk_text.strip()))
            parsed_df = parsed_df.rename(columns={"日": "date", "時間": "slot", "施設名": "ground_name"})
            parsed_df['date'] = pd.to_datetime(parsed_df['date']).dt.strftime('%Y-%m-%d')
            parsed_df['id'] = "S_" + parsed_df['date'] + "_" + parsed_df['slot'] + "_" + parsed_df['ground_name']
            parsed_df['year_month'] = parsed_df['date'].str[:7]
            parsed_df['status'] = "未割り当て"
            filtered_slots = slots_df[~slots_df['id'].isin(parsed_df['id'])]
            conn.update(worksheet="available_slots", data=pd.concat([filtered_slots, parsed_df], ignore_index=True))
            st.cache_data.clear()
            st.rerun()

    elif reg_mode == "CSVファイルから一括アップロード":
        uploaded_file = st.file_uploader("CSVファイルを選択", type=["csv"])
        if uploaded_file is not None:
            uploaded_df = pd.read_csv(uploaded_file)
            if st.button("💾 CSVを保存"):
                uploaded_df['date'] = uploaded_df['date'].astype(str)
                uploaded_df['id'] = "S_" + uploaded_df['date'] + "_" + uploaded_df['slot'] + "_" + uploaded_df['ground_name']
                uploaded_df['year_month'] = uploaded_df['date'].str[:7]
                uploaded_df['status'] = "未割り当て"
                filtered_slots = slots_df[~slots_df['id'].isin(uploaded_df['id'])]
                conn.update(worksheet="available_slots", data=pd.concat([filtered_slots, uploaded_df], ignore_index=True))
                st.cache_data.clear()
                st.rerun()

    st.markdown("---")
    st.subheader("② 次月の日程自動作成の実行")
    target_month_sched = st.selectbox("作成する対象月を選択", ["2026-07", "2026-08", "2026-09"], key="sb_month")
    current_month_slots = slots_df[(slots_df['year_month'] == target_month_sched) & (slots_df['status'] == "未割り当て")].copy()
    st.write(f"📊 未割り当て枠: {len(current_month_slots)} 件")
    
    if st.button("🔥 日程自動生成", type="primary"):
        current_month_slots['is_far'] = current_month_slots['ground_name'].map(ground_is_far)
        slots_input_list = current_month_slots.to_dict('records')
        current_pool = list(zip(pool_df['team1'], pool_df['team2']))
        monthly_ng_df = ng_df[ng_df['year_month'] == target_month_sched]
        ng_days_dict = dict(zip(monthly_ng_df['team'], monthly_ng_df['ng_date']))
        
        new_sched_df, rem_pool_list, filled_slot_ids = make_monthly_schedule(current_pool, slots_input_list, ng_days_dict, team_allow_far)
        
        if not new_sched_df.empty:
            conn.update(worksheet="schedule", data=pd.concat([sched_df, new_sched_df], ignore_index=True))
            conn.update(worksheet="match_pool", data=pd.DataFrame(rem_pool_list, columns=['team1', 'team2']))
            slots_df.loc[slots_df['id'].isin(filled_slot_ids), 'status'] = '割り当て済み'
            conn.update(worksheet="available_slots", data=slots_df)
            st.success("完了しました")
            st.rerun()

    if not sched_df.empty:
        display_sched = sched_df.copy()
        display_sched['GoogleMap_URL'] = display_sched['ground_name'].map(ground_maps)
        st.dataframe(display_sched, use_container_width=True)

# --- タブ3: 試合結果入力 ---
with tab3:
    st.header("🏆 試合結果入力")
    if sched_df.empty: st.warning("確定した日程がありません。")
    else:
        for idx, row in sched_df.iterrows():
            m_id = row['id']
            existing_res = res_df[res_df['id'] == m_id]
            current_status = existing_res['status'].values[0] if not existing_res.empty else "未消化"
            with st.expander(f"【{row['date']} {row['slot']}】 {row['team1']} vs {row['team2']} ({current_status})"):
                status = st.selectbox("試合ステータス", ["未消化", "通常消化", "雨天中止", "不戦敗"], key=f"st_{m_id}")
                if status == "通常消化":
                    sc1 = st.number_input("T1スコア", min_value=0, key=f"sc1_{m_id}")
                    sc2 = st.number_input("T2スコア", min_value=0, key=f"sc2_{m_id}")
                    if st.button("保存", key=f"save_{m_id}"):
                        new_res = pd.DataFrame([{"id": m_id, "status": "通常消化", "score": f"{sc1}-{sc2}"}])
                        conn.update(worksheet="results", data=pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True))
                        st.rerun()
                elif status == "雨天中止":
                    if st.button("雨天中止確定", key=f"rain_{m_id}"):
                        updated_sched = sched_df[sched_df['id'] != m_id]
                        conn.update(worksheet="schedule", data=updated_sched)
                        updated_pool = pd.concat([pd.DataFrame([{"team1": row['team1'], "team2": row['team2']}]), pool_df], ignore_index=True)
                        conn.update(worksheet="match_pool", data=updated_pool)
                        slots_df.loc[slots_df['id'] == m_id, 'status'] = '未割り当て'
                        conn.update(worksheet="available_slots", data=slots_df)
                        st.rerun()

# --- タブ4: 残試合数確認 ---
with tab4:
    st.header("📊 残試合数確認")
    st.subheader("🔥 未消化試合")
    st.dataframe(pool_df, use_container_width=True)
    
    st.markdown("リーグ全体の残り試合数の集計状況です。")
    today_str = datetime.now().strftime('%Y-%m-%d')
    remaining_data = []
    
    if not res_df.empty and 'id' in res_df.columns:
        exclude_ids = set(res_df[res_df['status'].isin(['通常消化', '不戦敗', '雨天中止'])]['id'].astype(str).tolist())
    else:
        exclude_ids = set()

    for team in all_teams:
        unallocated = ((pool_df['team1'] == team) | (pool_df['team2'] == team)).sum() if not pool_df.empty else 0
        past_unplayed = 0
        future_unplayed = 0
        if not sched_df.empty:
            unplayed_sched = sched_df[~sched_df['id'].astype(str).isin(exclude_ids)]
            team_sched = unplayed_sched[(unplayed_sched['team1'] == team) | (unplayed_sched['team2'] == team)]
            if not team_sched.empty:
                past_unplayed = (team_sched['date'].astype(str) < today_str).sum()
                future_unplayed = (team_sched['date'].astype(str) >= today_str).sum()
        remaining_data.append({
            "チーム名": team,
            "総残試合数": unallocated + past_unplayed + future_unplayed,
            "未日程 (プール内)": unallocated,
            "日程済 (過去の未消化)": past_unplayed,
            "日程済 (未来の未消化)": future_unplayed
        })
    remaining_df = pd.DataFrame(remaining_data).sort_values(by="総残試合数", ascending=False)
    st.dataframe(remaining_df, use_container_width=True, hide_index=True)

# --- タブ5: マスタメンテナンス ---
with tab5:
    st.header("⚙️ マスタメンテ・データ確認")
    m_tab1, m_tab2, m_tab3 = st.tabs(["🏟️ グラウンドマスタ編集", "🏃 チームマスタ編集", "📋 確保枠確認"])
    
    with m_tab1:
        # 型の再キャスト（エラー回避）
        grounds_df["is_far"] = grounds_df["is_far"].fillna(False).astype(bool)
        grounds_df["maps_url"] = grounds_df["maps_url"].fillna("").astype(str)
        
        edited_grounds_df = st.data_editor(grounds_df, num_rows="dynamic", use_container_width=True, key="master_grounds_editor", column_config={
            "name": st.column_config.TextColumn("グラウンド名", required=True),
            "is_far": st.column_config.CheckboxColumn("遠方フラグ"),
            "maps_url": st.column_config.TextColumn("GoogleMap URL")
        })
        if st.button("💾 グラウンドマスタを保存"):
            conn.update(worksheet="grounds", data=edited_grounds_df.fillna(""))
            st.rerun()
            
    with m_tab2:
        # 型の再キャスト（エラー回避）
        teams_df["allow_far"] = teams_df["allow_far"].fillna(False).astype(bool)
        
        edited_teams_df = st.data_editor(teams_df, num_rows="dynamic", use_container_width=True, key="master_teams_editor", column_config={
            "team": st.column_config.TextColumn("チーム名", required=True),
            "allow_far": st.column_config.CheckboxColumn("遠方対応可否")
        })
        if st.button("💾 チームマスタを保存"):
            conn.update(worksheet="teams", data=edited_teams_df.fillna(""))
            st.rerun()

    with m_tab3:
        st.subheader("📋 確保グラウンド枠の全履歴")
        st.dataframe(slots_df, use_container_width=True)