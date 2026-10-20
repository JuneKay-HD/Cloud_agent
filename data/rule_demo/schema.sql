CREATE INDEX idx_invoice_date ON Invoice(InvoiceDate);
CREATE INDEX idx_refund_invoice ON Refund(InvoiceId);
CREATE TABLE Customer(CustomerId INTEGER PRIMARY KEY, FirstName TEXT, LastName TEXT, Country TEXT);
CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER NOT NULL REFERENCES Customer,
            InvoiceDate TEXT NOT NULL, BillingCountry TEXT NOT NULL, Total REAL NOT NULL CHECK(Total >= 0));
CREATE TABLE Refund(RefundId INTEGER PRIMARY KEY, InvoiceId INTEGER NOT NULL REFERENCES Invoice,
            Amount REAL NOT NULL CHECK(Amount >= 0));
