"""Calculate rebelity and govity."""

import numpy as np
import pandas as pd

localpath = "brno/"

government_groups = ['ANO 2011', 'ODS a TOP 09', 'Lidovci a Starostové', 'Piráti', 'ČSSD VAŠI STAROSTOVÉ']

last_government = {
  'since': '2022-10-01',
  'until': np.inf,
}

vote_events = pd.read_csv(localpath + "data/vote_events.csv")
votes = pd.read_csv(localpath + "data/votes.csv")

# add government indicator
votes['in_gov'] = votes['group_id'].isin(government_groups)

# rebelity and govity
votes['vote_value'] = votes['option'].map({'yes': 1, 'no': -1, 'abstain': -1, 'not voting': -1, 'absent': 0})
votes['present'] = votes['option'].map({'yes': 1, 'no': 1, 'abstain': 1, 'not voting': 1, 'absent': 0})

# add group vote and merge back
pt = pd.pivot_table(votes, values='vote_value', index=['vote_event_id', 'group_id'], aggfunc=sum, fill_value=0).reset_index()
pt['group_way'] = np.sign(pt['vote_value'])
pt['group_way_abs'] = np.abs(np.sign(pt['vote_value']))

votes = votes.merge(pt.loc[:, ['vote_event_id', 'group_id', 'group_way', 'group_way_abs']], on=['vote_event_id', 'group_id'], how='left')

# add government vote and merge back
ptg = pd.pivot_table(votes, values=['vote_value'], index=['vote_event_id', 'in_gov'], dropna=False, aggfunc=sum, fill_value=0).reset_index()
ptgf = ptg[ptg['in_gov']]
ptgf.loc[:, 'gov_way'] = np.sign(ptgf['vote_value'])
ptgf['gov_way_abs'] = np.abs(np.sign(ptgf['vote_value']))

votes = votes.merge(ptgf.loc[:, ['vote_event_id', 'gov_way', 'gov_way_abs']], on=['vote_event_id'], how='left')

# rebeling
votes['rebeling'] = 0
votes.loc[votes['vote_value'] * votes['group_way'] == -1, ['rebeling']] = 1

rt = pd.pivot_table(votes, index=['voter_id'], values=['rebeling', 'group_way_abs'], aggfunc=np.sum).reset_index()
rt['rebelity'] = rt['rebeling'] / rt['group_way_abs']

# govity
votes['against_gov'] = 0
votes['possibly_against_gov'] = 0
votes.loc[votes['vote_value'] * votes['gov_way'] == -1, ['against_gov']] = 1
votes.loc[(votes['present'] == 1) & (votes['gov_way_abs'] == 1), ['possibly_against_gov']] = 1

gt = pd.pivot_table(votes, index=['voter_id'], values=['against_gov', 'possibly_against_gov'], aggfunc=np.sum).reset_index()
gt['govity'] = 1 - gt['against_gov'] / gt['possibly_against_gov']

# add last group
pt2 = pd.pivot_table(votes, values='group_id', index=['voter_id'], aggfunc='first').reset_index()
pt2['voter_id'] = pt2['voter_id'].astype(str)

# change column types for merge
rt['voter_id'] = rt['voter_id'].astype(pd.StringDtype())
gt['voter_id'] = gt['voter_id'].astype(pd.StringDtype())
pt2['voter_id'] = pt2['voter_id'].astype(pd.StringDtype())

# merge
pt = pt2.merge(rt, on='voter_id', how='left').merge(gt, on='voter_id', how='left')

# output v1
pt['rebel'] = round(10000 * pt['rebelity']) / 100
pt['gover'] = round(1000 * gt['govity']) / 10
pt.rename(columns={'possibly_against_gov': 'possible'}, inplace=True)
pt['with_gov'] = pt['possible'] - pt['against_gov']

# df.sort_values(by=['group_id'], inplace=True) # sort not working for czech characters
pt = pt.iloc[pt['group_id'].str.normalize('NFKD').argsort()] # https://stackoverflow.com/a/50217892/1666623

dfr = pt.loc[:, ['voter_id', 'group_way_abs', 'rebeling', 'rebel', 'group_id']]
dfr.to_csv(localpath + "data/rebelity.v1.csv", index=False)

dfg = pt.loc[:, ['voter_id', 'with_gov', 'possible', 'gover', 'group_id']]
dfg.to_csv(localpath + "data/govity.v1.csv", index=False)