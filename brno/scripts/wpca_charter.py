"""Prepare data for WPCA chart."""

import pandas as pd

localpath = "brno/"

rotate = {
  'column': 'name',
  'value': 'Matěj Hollan',
  'dims': [1, 1]
}

# variables
variables = ['id', 'name', 'party']
sortby = 'party'
variablesn = ['id', 'jméno', 'strana']


# data
wpca = pd.read_csv(localpath + "data/wpca.csv")
voters = pd.read_csv(localpath + "data/voters.csv")

# merge
wpca = wpca.merge(voters.loc[:, variables], left_on='voter_id', right_on='id', how='left')

# rotate
# find row
row = wpca.loc[wpca[rotate['column']] == rotate['value']]
# dims
for i in range(0, 2):
  if (rotate['dims'][i]) * row['dim' + str(i + 1)].values[0] < 0:
    wpca.loc[:, 'dim' + str(i + 1)] = wpca['dim' + str(i + 1)] * -1

# sort
wpca.sort_values(by=[sortby], inplace=True)

# rename columns
wpca.rename(columns=dict(zip(variables, variablesn)), inplace=True)

# save
wpca.to_csv(localpath + "data/wpca.v1.csv", index=False)
