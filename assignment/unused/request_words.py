from random import random, choice

word_list = [()]
with open('./server/data/grail') as f:
    lines = f.readlines()
    for _ in range(100):
        line = choice(lines).strip().split()
        while len(line) == 0:
            line = choice(lines).strip().split()
        word = choice(line)
        word_list.append(("grail", word))

with open('./server/data/shrek') as f:
    lines = f.readlines()
    for _ in range(100):
        line = choice(lines).strip().split()
        while len(line) == 0:
            line = choice(lines).strip().split()
        word = choice(line)
        word_list.append(("shrek", word))

with open('./server/data/shrek3') as f:
    lines = f.readlines()
    for _ in range(100):

        line = choice(lines).strip().split()
        while len(line) == 0:
            line = choice(lines).strip().split()
        word = choice(line)
        word_list.append(("shrek3", word))

with open('./word_list', 'w') as f:
    f.write('\n'.join([str(x) for x in word_list]))