"""Calculate attendance."""

import pandas as pd

localpath = "praha/"

vote_events = pd.read_csv(localpath + "data/vote_events.csv")
votes = pd.read_csv(localpath + "data/votes.csv")
voters = pd.read_csv(localpath + "data/voters.csv")

# attendance
pt = pd.pivot_table(votes, values='vote_event_id', index=['voter_id'], columns=['option'], aggfunc=len, fill_value=0).reset_index()
pt['attended'] = pt['yes'] + pt['no'] + pt['abstain'] + pt['not voting']
pt['possible'] = pt['attended'] + pt['absent']
pt['attendance'] = pt['attended'] / pt['possible']

# add last group
pt2 = pd.pivot_table(votes, values=['group_id', 'party_id'], index=['voter_id'], aggfunc='last').reset_index()

# change column types for merge
# pt['voter_id'] = pt['voter_id'].astype(pd.StringDtype())
# pt2['voter_id'] = pt2['voter_id'].astype(pd.StringDtype())

# merge
pt = pt.merge(pt2, on='voter_id', how='left')
pt = pt.merge(voters.loc[:, ['id', 'name']], left_on='voter_id', right_on='id', how='left')

# save
df = pt.loc[:, ['voter_id', 'name', 'attended', 'possible', 'attendance', 'group_id', 'party_id']]
df['účast'] = (df['attendance'] * 100).round(0).astype(int)
del df['attendance']
# df.sort_values(by=['group_id'], inplace=True) # sort not working for czech characters
df = df.iloc[df['group_id'].str.normalize('NFKD').argsort()] # https://stackoverflow.com/a/50217892/1666623
df.to_csv(localpath + "data/attendance.v1.csv", index=False)