"""Calculate attendance."""

import pandas as pd

localpath = "brno/"

vote_events = pd.read_csv(localpath + "data/vote_events.csv")
votes = pd.read_csv(localpath + "data/votes.csv")

# attendance
pt = pd.pivot_table(votes, values='vote_event_id', index=['voter_id'], columns=['option'], aggfunc=len, fill_value=0).reset_index()
pt['attended'] = pt['yes'] + pt['no'] + pt['abstain'] + pt['not voting']
pt['possible'] = pt['attended'] + pt['absent']
pt['attendance'] = pt['attended'] / pt['possible']
pt['voter_id'] = pt['voter_id'].astype(str)

# add last group
pt2 = pd.pivot_table(votes, values='group_id', index=['voter_id'], aggfunc='first').reset_index()
pt2['voter_id'] = pt2['voter_id'].astype(str)

# change column types for merge
pt['voter_id'] = pt['voter_id'].astype(pd.StringDtype())
pt2['voter_id'] = pt2['voter_id'].astype(pd.StringDtype())

# merge
pt = pt.merge(pt2, on='voter_id', how='left')

# save
df = pt.loc[:, ['voter_id', 'attended', 'possible', 'attendance', 'group_id']]
df['účast'] = (df['attendance'] * 100).round(0).astype(int)
del df['attendance']
df.to_csv(localpath + "data/attendance.v1.csv", index=False)