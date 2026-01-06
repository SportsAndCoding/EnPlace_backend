import bcrypt
print(bcrypt.hashpw(b'Found3RBitche$$!', bcrypt.gensalt()).decode())