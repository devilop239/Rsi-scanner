import pandas as pd
import numpy as np

idx1 = pd.MultiIndex.from_product([['Close', 'Open'], ['A', 'B']])
df1 = pd.DataFrame(np.random.randn(3, 4), columns=idx1)

idx2 = pd.MultiIndex.from_product([['Close', 'Open'], ['C', 'D']])
df2 = pd.DataFrame(np.random.randn(3, 4), columns=idx2)

df3 = pd.concat([df1, df2], axis=1)
print(df3.columns)
