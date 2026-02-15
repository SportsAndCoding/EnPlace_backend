import bcrypt
print(bcrypt.hashpw(b'PtRy3Ltg8ivNPaD', bcrypt.gensalt()).decode())