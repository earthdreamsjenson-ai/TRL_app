import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
from datetime import datetime, timedelta
import io
from collections import Counter

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

# 【エラー対策】データ読み込み直後にフラグ列などを整理
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
    "🛠️ グラウンド枠登録・日程作成", 
    "🏆 試合結果入力", 
    "📊 残試合数確認",
    "⚙️ マスタメンテナンス・データ確認"
])

# --- タブ1: NG日登録 ---
with tab1:
    st.header("📢 NG日登録")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        select_team = st.selectbox("あなたのチーム名を選択してください", ["選択してください"] + all_teams)
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
        select_ng_date = st.selectbox("試合NGにする日を選択（次月の日曜日のみ）", options=next_month_sundays, format_func=lambda x: x.strftime('%Y-%m-%d（日）'))
        target_month = str(select_ng_date)[:7]
        st.caption(f"対象月（自動取得）: **{target_month}**")
        
    if select_team != "選択してください":
        if st.button("💾 NG日を登録・上書きする", type="primary"):
            new_ng_row = pd.DataFrame([{"team": select_team, "year_month": target_month, "ng_date": str(select_ng_date)}])
            filtered_ng = ng_df[~((ng_df['team'] == select_team) & (ng_df['year_month'] == target_month))]
            updated_ng_df = pd.concat([filtered_ng, new_ng_row], ignore_index=True)
            conn.update(worksheet="unavailable_days", data=updated_ng_df)
            st.cache_data.clear()
            st.success("保存しました！")
            st.rerun()
    st.subheader("👀 現在の各チームのNG日登録状況")
    st.dataframe(ng_df, use_container_width=True)

# --- タブ2: グラウンド枠登録・日程作成 ---
with tab2:
    st.header("🛠️ グラウンド枠登録・日程作成")
    # (中略: タブ2の内容は変更なしのため省略。実際には元のコードの「with tab2」の内容をすべて記述してください)
    st.info("タブ2のグラウンド枠登録・日程作成ロジック（省略）")

# --- タブ3: 試合結果入力 ---
with tab3:
    st.header("🏆 試合結果入力")
    # (中略: タブ3の内容は変更なしのため省略)
    st.info("タブ3の試合結果入力ロジック（省略）")

# --- タブ4: 残試合数確認（移動済み） ---
with tab4:
    st.header("📊 各チームの残試合数確認")
    
    # 【移動】ここに以前Tab4にあったプール確認を配置
    st.subheader("🔥 残りの未消化試合プール")
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
    st.header("⚙️ マスタメンテナンス・データ確認")
    m_tab1, m_tab2, m_tab3 = st.tabs(["🏟️ グラウンドマスタ編集", "🏃 チームマスタ編集", "📋 確保枠確認"])
    
    with m_tab1:
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