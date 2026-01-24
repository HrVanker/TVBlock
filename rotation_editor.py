import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import os

CONFIG_FILE = "station_config.json"

class RotationEditor(tk.Toplevel):
    def __init__(self, parent, library_keys, callback_refresh):
        super().__init__(parent)
        self.title("Rotation Group Editor")
        self.geometry("800x600")
        
        self.library_keys = sorted(library_keys) # List of all show names
        self.callback = callback_refresh # Function to call when we save (to update main UI)
        self.groups = {} # Will hold the loaded rotation_groups dictionary
        
        self.load_groups()
        self.create_widgets()
        
        # Select the first group by default if exists
        if self.group_list.size() > 0:
            self.group_list.selection_set(0)
            self.on_group_select(None)

    def load_groups(self):
        """Loads just the rotation_groups part of the config"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                self.groups = data.get("rotation_groups", {})
        else:
            self.groups = {}

    def save_groups(self):
        """Writes changes back to JSON"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {}
            
        data["rotation_groups"] = self.groups
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=4)
            
        messagebox.showinfo("Saved", "Rotation Groups updated successfully!")
        self.callback() # Tell main app to refresh
        self.destroy() # Close window

    def create_widgets(self):
        # --- LEFT PANE: List of Groups ---
        left_frame = tk.Frame(self, padx=10, pady=10, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        tk.Label(left_frame, text="Rotation Groups", font=("Arial", 12, "bold")).pack(anchor=tk.W)
        
        self.group_list = tk.Listbox(left_frame, height=20, font=("Arial", 11))
        self.group_list.pack(fill=tk.BOTH, expand=True, pady=5)
        self.group_list.bind('<<ListboxSelect>>', self.on_group_select)
        
        # Populate List
        for g in sorted(self.groups.keys()):
            self.group_list.insert(tk.END, g)

        # Group Buttons
        btn_frame = tk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(btn_frame, text="+ New", command=self.add_group).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(btn_frame, text="- Delete", command=self.delete_group).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # --- RIGHT PANE: Checkboxes for Shows ---
        right_frame = tk.Frame(self, padx=10, pady=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.lbl_editor = tk.Label(right_frame, text="Select Shows for Group:", font=("Arial", 12, "bold"))
        self.lbl_editor.pack(anchor=tk.W)
        
        # Scrollable Frame for Checkboxes
        canvas = tk.Canvas(right_frame)
        scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = tk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Dictionary to hold the checkbox variables { "Show Name": BooleanVar }
        self.check_vars = {} 
        
        # Create a checkbox for EVERY show in the library
        for show in self.library_keys:
            var = tk.BooleanVar()
            chk = tk.Checkbutton(self.scrollable_frame, text=show, variable=var, font=("Arial", 10), command=self.on_checkbox_click)
            chk.pack(anchor=tk.W, padx=5, pady=2)
            self.check_vars[show] = var

        # --- BOTTOM: Save Button ---
        bottom_frame = tk.Frame(self, pady=10)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Button(bottom_frame, text="SAVE CHANGES", bg="green", fg="white", font=("Arial", 12, "bold"), command=self.save_groups).pack()

    def add_group(self):
        name = simpledialog.askstring("New Group", "Enter name for new rotation group:")
        if name:
            if name in self.groups:
                messagebox.showerror("Error", "Group already exists!")
                return
            self.groups[name] = []
            self.group_list.insert(tk.END, name)
            self.group_list.selection_clear(0, tk.END)
            self.group_list.selection_set(tk.END)
            self.on_group_select(None)

    def delete_group(self):
        sel = self.group_list.curselection()
        if not sel: return
        name = self.group_list.get(sel[0])
        
        if messagebox.askyesno("Confirm", f"Delete group '{name}'?"):
            del self.groups[name]
            self.group_list.delete(sel[0])
            # Clear checkboxes
            for var in self.check_vars.values():
                var.set(False)

    def on_group_select(self, event):
        sel = self.group_list.curselection()
        if not sel: return
        name = self.group_list.get(sel[0])
        
        self.lbl_editor.config(text=f"Editing: {name}")
        
        current_members = self.groups[name]
        
        # Update checkboxes
        for show, var in self.check_vars.items():
            if show in current_members:
                var.set(True)
            else:
                var.set(False)

    def on_checkbox_click(self):
        # Update the internal dictionary immediately when clicked
        sel = self.group_list.curselection()
        if not sel: return
        name = self.group_list.get(sel[0])
        
        new_list = []
        for show, var in self.check_vars.items():
            if var.get():
                new_list.append(show)
        
        self.groups[name] = new_list